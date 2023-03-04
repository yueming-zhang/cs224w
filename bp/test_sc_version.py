import pytest
import boto3, botocore
from botocore.client import ClientError
from unittest.mock import patch
from datetime import datetime

import sys, os, inspect, json, time
import aws_cdk as cdk
from aws_cdk import assertions
from pathlib import Path
import zipfile

root = Path(__file__).parents[2]
sys.path.insert(0, str(root))

from mlops_sm_project_template_rt.pipeline_stack import CoreStage, ServiceCatalogStack
from mlops_sm_project_template_rt.permission_boundary import PermissionBoundaryAspect
from mlops_sm_project_template_rt.config.constants import (
    DEFAULT_DEPLOYMENT_REGION,
    PIPELINE_ACCOUNT,
    PIPELINE_ACCOUNT_NAME,
    STAGING_PIPELINE_ACCOUNT,
    get_code_bucket_name,
    get_sc_prod_version,
    get_local_prod_version
)
pipeline_env = cdk.Environment(account=PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
template_name = 'Arima'

def test_sc_list(mgmt_dev_env):
    """
    verify the Service Catalog portfilio is deployed to the dev account
    """
    client = boto3.client("servicecatalog")

    portfolios = client.list_portfolios()
    pf_list = [
        f
        for f in portfolios["PortfolioDetails"]
        if f["DisplayName"] == "SageMaker Organization Templates"
    ]

    assert len(pf_list) == 1

def test_sc_load_prod_json(mgmt_dev_env):
    prod_name = 'Arima'
    _sc = boto3.client("servicecatalog")

    prod_desc=_sc.describe_product(Name=prod_name)
    provisioning_artifact_id = prod_desc['ProvisioningArtifacts'][0]['Id']

    pa_desc = _sc.describe_provisioning_artifact(
        ProductId=prod_desc['ProductViewSummary']['ProductId'],
        ProvisioningArtifactId=provisioning_artifact_id
    )

    version = pa_desc['ProvisioningArtifactDetail']['Name']

    template_url = pa_desc['Info']['TemplateUrl']

    _s3 = boto3.resource('s3')

    s3_bucket = template_url.split('/')[-2]
    s3_file = template_url.split('/')[-1]
    content_object = _s3.Object(s3_bucket, s3_file)
    file_content = content_object.get()['Body'].read().decode('utf-8')
    json_content = json.loads(file_content)
    print(json_content['Resources'])


def test_sc_stack(mgmt_dev_env):
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


def test_code_stack(mgmt_dev_env):
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


def test_newer_version_unavailable_staging(mgmt_dev_env):
    '''
    when no newer version is available, the stack should use the original tempalte
    '''
    template_name = 'Arima'
    pipeline_env = cdk.Environment(account=STAGING_PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
    stage = cdk.App()

    new_version = '1.0.1'
    cur_version = '1.2.7'
    with patch('mlops_sm_project_template_rt.config.constants.get_local_prod_version') as mock_local:
        mock_local.return_value = new_version
        with patch('mlops_sm_project_template_rt.config.constants.get_sc_prod_version') as mock_sc:
            mock_sc.return_value = cur_version
            stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)

    template = assertions.Template.from_stack(stack)        
    res = template.to_json()['Resources']

    the_stack = [res[sc] for sc in res if res[sc]['Type'] == 'AWS::ServiceCatalog::CloudFormationProduct' and res[sc]['Properties']['Name']==template_name][0]
    assert the_stack['Properties']['ProvisioningArtifactParameters'][0]['Name'] == cur_version

    generated_template_path = stack.get_generated_template(template_name)

    res = json.loads(open(generated_template_path).read())['Resources']

    versions = [res[sc]['Properties']['Tags'][2]['Value'] for sc in res if 'Tags' in res[sc]['Properties'] and 
                len(res[sc]['Properties']['Tags']) >= 2 and res[sc]['Properties']['Tags'][2]['Key'] == 'version']
    for v in versions:
        assert v == new_version

def test_newer_version_unavailable_staging_actual(mgmt_dev_env):
    '''
    use the actual version to test the scenario: when no newer version is available, 
    the stack should use the original tempalte
    '''
    template_name = 'Arima'
    pipeline_env = cdk.Environment(account=STAGING_PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
    stage = cdk.App()

    new_version = '1.0.0'
    with patch('mlops_sm_project_template_rt.config.constants.get_local_prod_version') as mock_local:
        mock_local.return_value = new_version
        stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)

    cur_version = get_sc_prod_version(template_name)
    assert cur_version >= new_version

    template = assertions.Template.from_stack(stack)        
    res = template.to_json()['Resources']

    the_stack = [res[sc] for sc in res if res[sc]['Type'] == 'AWS::ServiceCatalog::CloudFormationProduct' and res[sc]['Properties']['Name']==template_name][0]
    assert the_stack['Properties']['ProvisioningArtifactParameters'][0]['Name'] == cur_version

    generated_template_path = stack.get_generated_template(template_name)

    res = json.loads(open(generated_template_path).read())['Resources']

    versions = [res[sc]['Properties']['Tags'][2]['Value'] for sc in res if 'Tags' in res[sc]['Properties'] and 
                len(res[sc]['Properties']['Tags']) >= 2 and res[sc]['Properties']['Tags'][2]['Key'] == 'version']
    assert len(versions) > 10
    for v in versions:
        assert v == cur_version


def test_newer_version_available_staging_actual(mgmt_dev_env):
    '''
    use the actual version to test the scenario: when newer version is available, 
    the stack should generate new tempalte with new version
    '''
    pipeline_env = cdk.Environment(account=STAGING_PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
    stage = cdk.App()

    new_version = '9.9.9'
    with patch('mlops_sm_project_template_rt.config.constants.get_local_prod_version') as mock_local:
        mock_local.return_value = new_version
        stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)

    cur_version = get_sc_prod_version(template_name)
    assert cur_version < new_version

    template = assertions.Template.from_stack(stack)        
    res = template.to_json()['Resources']

    the_stack = [res[sc] for sc in res if res[sc]['Type'] == 'AWS::ServiceCatalog::CloudFormationProduct' and res[sc]['Properties']['Name']==template_name][0]
    assert the_stack['Properties']['ProvisioningArtifactParameters'][0]['Name'] == new_version

    generated_template_path = stack.get_generated_template(template_name)

    res = json.loads(open(generated_template_path).read())['Resources']

    versions = [res[sc]['Properties']['Tags'][2]['Value'] for sc in res if 'Tags' in res[sc]['Properties'] and 
                len(res[sc]['Properties']['Tags']) >= 2 and res[sc]['Properties']['Tags'][2]['Key'] == 'version']
    assert len(versions) > 10
    for v in versions:
        assert v == new_version

def new_get_sc_prod_version(name):
    if name == template_name:
        return '1.2.7'
    else:
        return '0.0.1'

def test_code_stack_staging_no_new_version(mgmt_staging_env):
    '''
    when on dev, regardless newer version is available, the code stack use the local version
    '''
    pipeline_env = cdk.Environment(account=STAGING_PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)

    new_version = '1.0.0'
    cur_version = '1.2.7'
    with patch('mlops_sm_project_template_rt.config.constants.get_local_prod_version') as mock_local:
        mock_local.return_value = new_version
        with patch('mlops_sm_project_template_rt.config.constants.get_sc_prod_version', new=new_get_sc_prod_version) as mock_sc:
            stage = CoreStage(cdk.App(), "DEV", env = pipeline_env)
    template = assertions.Template.from_stack(stage.shared_code_stack)
    template.resource_count_is("AWS::S3::Bucket", 1)
    template.resource_count_is("Custom::CDKBucketDeployment", 1)

    res = template.to_json()['Resources']

    zip_path = [f for f in stage.shared_code_stack.zips_app if template_name in f][0]

    # load the zip file, unzip it, and verify __version__.py exists
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall('/tmp')
        with zipfile.ZipFile(f'/tmp/{template_name}-build_app.zip', 'r') as zip_ref:
            zip_ref.extractall(f'/tmp/{template_name}-tmp')

    assert os.path.exists(f'/tmp/{template_name}-tmp/__version__.py')
    
    # verify the extracted version in __version__.py is the same as the version in the folder
    template_path = f'{Path(__file__).parents[2]}/templates'
    new_version = get_local_prod_version(template_path, template_name)
    with open(f'/tmp/{template_name}-tmp/__version__.py', 'r') as f:
        content = f.read()
        assert new_version in content

    pass
