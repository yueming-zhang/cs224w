from re import template
import aws_cdk as cdk

from aws_cdk import (
    assertions,
    Aspects,
    aws_iam as iam,
    aws_s3_assets as s3_assets,
    aws_s3 as s3,
    aws_kms as kms,
    aws_servicecatalog_alpha as servicecatalog_alpha,
    aws_servicecatalog as servicecatalog,
    aws_ssm as ssm,
)

import sys, os, inspect, json
from unittest.mock import patch

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))  # type: ignore
parentdir = os.path.dirname(os.path.dirname(currentdir))
sys.path.insert(0, parentdir)
# os.chdir(parentdir)


from templates.Abalone.AbaloneStack import AbaloneStack
from mlops_sm_project_template_rt.pipeline_stack import CoreStage, PipelineStack, ServiceCatalogStack, GovernanceStack, PipelineCustomTriggerStack
from mlops_sm_project_template_rt.permission_boundary import PermissionBoundaryAspect
from mlops_sm_project_template_rt.config.constants import (
    DEFAULT_DEPLOYMENT_REGION,
    PIPELINE_ACCOUNT,
    PIPELINE_ACCOUNT_NAME,
    STAGING_PIPELINE_ACCOUNT,
    get_code_bucket_name
)

pipeline_env = cdk.Environment(account=PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)

# example tests. To run these tests, uncomment this file along with the example
# resource in mlops_sm_project_template_rt_v2/mlops_sm_project_template_rt_v2_stack.py
def test_mlops_stack():
    """
    verify the stack contains 2 code pipeline, and 3 code build project
    """
    app = cdk.App()
    stack = AbaloneStack(app, "mlops-stacks", env=pipeline_env)
    template = assertions.Template.from_stack(stack)

    template.resource_count_is("AWS::CodeBuild::Project", 2)
    pipelines = [
        v
        for v in template.to_json()["Resources"].values()
        if v["Type"] == "AWS::CodePipeline::Pipeline"
    ]

    assert len(pipelines) == 1

def test_pipeline_stack():
    """
    verify the pipeline stack contains one pipeline, and mutliple code build projects
    """
    app = cdk.App()
    stack = PipelineStack(app, "pipeline-stack", pipeline_type='DEV', env=pipeline_env)
    template = assertions.Template.from_stack(stack)
    template.resource_count_is("AWS::CodePipeline::Pipeline", 1)

    template_str = json.dumps(template.to_json())
    # assert f'codecommit:eu-west-1:{PIPELINE_ACCOUNT}:ml-devops-sagemaker-studio-replica' in template_str

def test_pipeline_custom_trigger():
    """
    verify the pipeline stack contains one pipeline, and mutliple code build projects
    """
    app = cdk.App()
    stack = PipelineCustomTriggerStack(app, "pipeline-stack", env=pipeline_env)
    template = assertions.Template.from_stack(stack)
    # template.resource_count_is("AWS::CodePipeline::Pipeline", 1)
    # template.resource_count_is("AWS::CodeBuild::Project", 18)

    # template_str = json.dumps(template.to_json())
    # assert f'codecommit:eu-west-1:{PIPELINE_ACCOUNT}:ml-devops-sagemaker-studio-replica' in template_str


def test_generate_template():
    '''    
    Generating CFN template for stack: MLOpsApp-dev, with kwargs: {'env': Environment(account='387661743389', region='eu-west-1')}
    
    calling syntax: generate_template(AbaloneStack, f"MLOpsApp-{stage_name}", **kwargs)

    verify that all generated roles have permission boundary (note the service catalog verification doesn't verify
    the included template, so we verify it here directly)
    '''

    stage = cdk.App()
    pipeline_env = cdk.Environment(account=PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
    # stack = AbaloneStack(stage, "test-mlops-stack", synthesizer=cdk.BootstraplessSynthesizer(), env=pipeline_env)        
    stack = AbaloneStack(stage, "test-mlops-stack", env=pipeline_env)        

    Aspects.of(stage).add(
        PermissionBoundaryAspect(
            f"arn:aws:iam::{PIPELINE_ACCOUNT}:policy/{PIPELINE_ACCOUNT_NAME}-pol_PlatformUserBoundary"
        )
    )

    assembly = stage.synth()

    for stack in assembly.stacks:
        template_path = stack.template_full_path
        res = json.loads(open(template_path).read())['Resources']
        roles = [item for item in res.items() if item[1]['Type'] == 'AWS::IAM::Role']
        for role in roles:
            assert "-pol_PlatformUserBoundary" in json.dumps(role), f"Role {role[0]} does not have permission boundary"    


def test_servicecatalog_permission_boundary():
    '''
    create a service catalog stack, verify no permission boundary is added,
    Then use Aspect applies a visitor, and verify the permission boundary is added
    
    '''
    pipeline_env = cdk.Environment(account=PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
    # pipeline_env = cdk.Environment(account=STAGING_PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)

    stage = cdk.App()
    stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)
    template = assertions.Template.from_stack(stack)    
    res = template.to_json()['Resources']
    roles = [item for item in res.values() if item['Type'] == 'AWS::IAM::Role']
    for role in roles:
        assert "-pol_PlatformUserBoundary" not in json.dumps(role)    


    stage = cdk.App()
    stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)
    cdk.Aspects.of(stack).add(
        PermissionBoundaryAspect(
            f"arn:aws:iam::{PIPELINE_ACCOUNT}:policy/{PIPELINE_ACCOUNT_NAME}-pol_PlatformUserBoundary"
        )
    )
    # after apply Aspect, verify the Permission Boundary is added
    template = assertions.Template.from_stack(stack)        
    res = template.to_json()['Resources']
    roles = [item for item in res.values() if item['Type'] == 'AWS::IAM::Role']
    for role in roles:
        assert "-pol_PlatformUserBoundary" in json.dumps(role)    

    generated_template_path = stack.get_generated_template('Abalone')
    res = json.loads(open(generated_template_path).read())['Resources']
    roles = [item for item in res.items() if item[1]['Type'] == 'AWS::IAM::Role']
    for role in roles:
        assert "-pol_PlatformUserBoundary" in json.dumps(role), f"Role {role[0]} does not have permission boundary"    

    pass


def test_servicecatalog_dynamic_account():
    '''
    create a service catalog stack, verify no permission boundary is added,
    Then use Aspect applies a visitor, and verify the permission boundary is added

    '''
    pipeline_env = cdk.Environment(account=PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)

    stage = cdk.App()
    stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)
    generated_template_path = stack.get_generated_template('Abalone')

    res = open(generated_template_path).read()
    assert f'resolve:ssm:/mlops/dev/account_id' in res

    res = json.loads(res)['Resources']
    for item in res.values():
        if item['Type'] == 'AWS::Lambda::Function':
            #lambda function code is sourced from gov account
            assert f'{PIPELINE_ACCOUNT}' in str(item)
            assert item['Properties']['Code']['S3Bucket'] == get_code_bucket_name(pipeline_env.account)

            # cdk generate a dynamic guid for the custom resource provider, which breaks the service catalog stack,
            # we replace it with a fixed name
            if 'Description' in item['Properties'] and 'AWS CDK resource provider framework - onEvent' in item['Properties']['Description']:
                assert item['Properties']['Code']['S3Key'] == 'custom-resource-provider-onevent.zip'
            continue
        if item['Type'] == 'AWS::CodeCommit::Repository':
            #seed repo code is sourced from gov account
            assert f'{PIPELINE_ACCOUNT}' in str(item)
            assert item['Properties']['Code']['S3']['Bucket'] == get_code_bucket_name(pipeline_env.account)
            continue
        # other resources are from the target account depoyed by service catalog
        if f'{PIPELINE_ACCOUNT}' in str(item):
            assert False
            pass
    pass

def test_servicecatalog_kms():
    '''
    create a service catalog stack, verify no permission boundary is added,
    Then use Aspect applies a visitor, and verify the permission boundary is added
    
    '''
    stage = cdk.App()
    stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)
    generated_template_path = stack.get_generated_template('Abalone')

    res = open(generated_template_path).read()
    res = json.loads(res)['Resources']
    kms_list = [item for item in res.values() if item['Type'] == 'AWS::KMS::Key']
    assert len(kms_list) == 1

def test_code_stack():
    stage = CoreStage(cdk.App(), "DEV", env = pipeline_env)
    template = assertions.Template.from_stack(stage.shared_code_stack)
    template.resource_count_is("AWS::S3::Bucket", 1)
    template.resource_count_is("Custom::CDKBucketDeployment", 1)

    res = template.to_json()['Resources']
    for item in res.values():
        if item['Type'] == 'AWS::IAM::Role':
            #lambda will use pipeline account
            assert 'PermissionsBoundary' in str(item)            

    pass

def test_gov_stack():
    """
    Verify dynamo db table is created in governance stack

    """
    stage = CoreStage(cdk.App(), "DEV", env=pipeline_env) 
    template = assertions.Template.from_stack(stage.governance_stack)
    template.resource_count_is("AWS::DynamoDB::Table", 1)
    pass