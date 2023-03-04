# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# SPDX-License-Identifier: MIT-0
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of this
# software and associated documentation files (the "Software"), to deal in the Software
# without restriction, including without limitation the rights to use, copy, modify,
# merge, publish, distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
# INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
# PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
# HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

from pyexpat import model
from aws_cdk import (
    Aws,
    Aspects,
    BundlingOptions,
    BundlingOutput,
    CfnParameter,
    DockerImage,
    Stack,
    Stage,
    Tags,
    aws_iam as iam,
    aws_s3_assets as s3_assets,
    aws_s3 as s3,
    aws_kms as kms,
    aws_servicecatalog_alpha as servicecatalog_alpha,
    aws_servicecatalog as servicecatalog,
    aws_ssm as ssm,
)
import aws_cdk
import json
from datetime import datetime
from os import path, listdir, sys
import inspect
import importlib
import boto3
import requests

from constructs import Construct

from mlops_sm_project_template_rt.constructs.ssm_construct import SSMConstruct
from mlops_sm_project_template_rt.permission_boundary import PermissionBoundaryAspect

from mlops_sm_project_template_rt.config.constants import (
    DEV_ACCOUNT,
    DEV_ACCOUNT_NAME,
    PIPELINE_ACCOUNT,
    PREPROD_ACCOUNT, 
    PREPROD_ACCOUNT_NAME,
    PROD_ACCOUNT, 
    PROD_ACCOUNT_NAME,
    PROD_REGION,
    sc_prod_launch_role_name,
    get_code_bucket_name,
    get_act_name_from_id,
    get_sc_prod_version,
    get_local_prod_version,
    DEFAULT_DEPLOYMENT_REGION,
    FEATURE_DEV_ACCOUNT,
    FEATURE_DEV_ACCOUNT_NAME,
    FEATURE_GOV_ACCOUNT,
    FEATURE_GOV_ACCOUNT_NAME
)

# Create a Portfolio and Product
# see: https://docs.aws.amazon.com/cdk/api/latest/python/aws_cdk.aws_servicecatalog.html
class ServiceCatalogStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.act_id = kwargs['env'].account
        assert len(self.act_id) == 12

        stage_name = Stage.of(self).stage_name.lower()

        self.execution_role_arn = CfnParameter(
            self,
            "ExecutionRoleArn",
            type="AWS::SSM::Parameter::Value<String>",
            description="The SageMaker Studio execution role",
            min_length=1,
            default="/mlops/role/lead",
        )

        portfolio_name = CfnParameter(
            self,
            "PortfolioName",
            type="String",
            description="The name of the portfolio",
            default="SageMaker Organization Templates",
            min_length=1,
        )

        self.portfolio_owner = CfnParameter(
            self,
            "PortfolioOwner",
            type="String",
            description="The owner of the portfolio",
            default="administrator",
            min_length=1,
            max_length=50,
        )

        self.product_version = CfnParameter(
            self,
            "ProductVersion",
            type="String",
            description="The product version to deploy",
            default="1.0",
            min_length=1,
        )

        self.products_launch_role = self.get_products_launch_role()
        self.portfolio = servicecatalog_alpha.Portfolio(
            self,
            "Portfolio",
            display_name=portfolio_name.value_as_string,
            provider_name=self.portfolio_owner.value_as_string,
            description="BP SageMaker Organization Templates",
        )

        # Create portfolio associate that depends on products
        self.portfolio_association = servicecatalog.CfnPortfolioPrincipalAssociation(
            self,
            f"PortfolioPrincipalAssociation",
            portfolio_id=self.portfolio.portfolio_id,
            principal_arn=self.execution_role_arn.value_as_string,
            principal_type="IAM",
        )

        # a service catalog could contain multiple products, below code loop through all folders under
        # templates, and add each as a product
        parent_dir = path.dirname(path.abspath(inspect.getfile(inspect.currentframe())))  # type: ignore
        templates_root = f'{path.dirname(parent_dir)}/templates'
        product_id_list = []
        self.generated_template_path_list = []
        if templates_root not in sys.path:
            sys.path.insert(0, templates_root)


        from mlops_sm_project_template_rt.config.constants import get_sc_prod_version, get_local_prod_version
        for template_dir in listdir(templates_root):
            if not path.isdir(path.join(templates_root, template_dir)):
                continue

            for template_file in listdir(path.join(templates_root, template_dir)):
                if template_file == f'{template_dir}Stack.py': 

                    new_version = get_local_prod_version(templates_root, template_dir)
                    cur_version = get_sc_prod_version(template_dir)

                    if self.account not in[PIPELINE_ACCOUNT, FEATURE_GOV_ACCOUNT] and new_version < '1.0':
                        # we only release version 1.0 or above to non-dev account
                        continue

                    templ_prod_id = self.add_template_to_portfolio(stage_name, template_dir, cur_version, new_version, **kwargs)
                    product_id_list.append(templ_prod_id)

        # role_constraint.add_depends_on(portfolio_association)
        if self.account == PIPELINE_ACCOUNT:
            SSMConstruct(self, "MLOpsSSM", DEV_ACCOUNT, DEV_ACCOUNT_NAME, PREPROD_ACCOUNT, PROD_ACCOUNT, PROD_REGION, product_id_list)
        elif self.account == FEATURE_GOV_ACCOUNT:
             SSMConstruct(self, "MLOpsSSM", FEATURE_DEV_ACCOUNT, FEATURE_DEV_ACCOUNT_NAME, PREPROD_ACCOUNT, PROD_ACCOUNT, PROD_REGION, product_id_list)



    def add_template_to_portfolio(self, stage_name, template_dir, current_version, new_version, **kwargs):
        '''
        one portfolio can have multiple products, here we add the product to the portfolio
        
        '''
        module = importlib.import_module(f'{template_dir}.{template_dir}Stack')
        template_class = getattr(module, f'{template_dir}Stack')
        if new_version > current_version or self.account in [PIPELINE_ACCOUNT, FEATURE_GOV_ACCOUNT]:
            json_path = self.generate_template(template_class, f"{template_dir}-{stage_name}", version=new_version, **kwargs)   
            final_version = new_version
        else:
            json_path = self.get_existing_template(template_dir)
            final_version = current_version

        self.generated_template_path_list.append(json_path)
        deploy_product = servicecatalog_alpha.CloudFormationProduct(
            self,
            f"DeployProduct{template_dir}",
            owner=self.portfolio_owner.value_as_string,
            product_name=template_dir,
            product_versions=[
                servicecatalog_alpha.CloudFormationProductVersion(
                    cloud_formation_template=servicecatalog_alpha.CloudFormationTemplate.from_asset(json_path),
                    product_version_name=final_version,# self.product_version.value_as_string,
                )
            ],
            description=f"{template_class.description()} (source account = {get_act_name_from_id(self.account)}, release time = {datetime.now()})",
        )

        self.portfolio_association.node.add_dependency(deploy_product)

        # Add product tags, and create role constraint for each product

        self.portfolio.add_product(deploy_product)

        Tags.of(deploy_product).add(key="sagemaker:studio-visibility", value="true")

        role_constraint = servicecatalog.CfnLaunchRoleConstraint(
            self,
            f"LaunchRoleConstraint{template_dir}",
            portfolio_id=self.portfolio.portfolio_id,
            product_id=deploy_product.product_id,
            local_role_name=self.products_launch_role.role_name, # use role_name for cross-account support
            description=f"Launch as {self.products_launch_role.role_name}",
            # role_arn=products_launch_role.role_arn,
            # description=f"Launch sc as {products_launch_role.role_arn}",
        )

        role_constraint.add_depends_on(self.portfolio_association)

        return [template_dir, deploy_product.product_id]

    def get_generated_template(self, name: str) -> str:
        '''
        this function is for testing purpose only, to get the generated template path
        '''
        for template in self.generated_template_path_list:
            if name in template:
                return template
        raise ValueError(f"template {name} not found")

    def get_products_launch_role(self):
        '''
        cross account sharing of service catalog requires create launch contraint by role name (rather than by arn)
        to support service catalog launch contraint by role name, we use a pre-created launch role
        
        '''
        role_arn = f'arn:aws:iam::{get_act_name_from_id(self.account)}:role/{sc_prod_launch_role_name}'
        products_launch_role = iam.Role.from_role_arn(self, "ProductLaunchRole", role_arn)
        return products_launch_role

    def export_ssm(self, key: str, param_name: str, value: str):
        param = ssm.StringParameter(self, key, parameter_name=param_name, string_value=value)

    def generate_template(self, stack: Stack, stack_name: str, version, **kwargs):
        """Create a CFN template from a stack

        Args:
            stack (cdk.Stack): cdk Stack to synthesize into a CFN template
            stack_name (str): Name to assign to the stack

        Returns:
            [str]: path of the CFN template
        """

        print (f'Generating CFN template for stack: {stack_name}, with kwargs: {kwargs}')
        stage = aws_cdk.App()
        stack = stack(stage, stack_name, version, **kwargs)
        # stack = stack(stage, stack_name, synthesizer=aws_cdk.BootstraplessSynthesizer(), **kwargs)        
        aws_cdk.Aspects.of(stack).add(
            PermissionBoundaryAspect(
                f"arn:aws:iam::{self.act_id}:policy/{get_act_name_from_id(self.act_id)}-pol_PlatformUserBoundary"
            )
        )

        assembly = stage.synth()
        template_full_path = assembly.stacks[0].template_full_path

        processed_path = self.post_processing(template_full_path)

        return processed_path
    
    def get_existing_template(self, sc_prod_name):
        """when the product version is not changed, we can use the existing template

        Args:
            sc_prod_name (str): name of the service catalog product

        Returns:
            [str]: path of the CFN template
        """

        print (f'Retrieve CFN template from sc_prod: {sc_prod_name}')

        # get the existing template from service catalog
        _sc = boto3.client("servicecatalog")

        prod_desc=_sc.describe_product(Name=sc_prod_name)
        provisioning_artifact_id = prod_desc['ProvisioningArtifacts'][0]['Id']

        pa_desc = _sc.describe_provisioning_artifact(
            ProductId=prod_desc['ProductViewSummary']['ProductId'],
            ProvisioningArtifactId=provisioning_artifact_id
        )

        template_url = pa_desc['Info']['TemplateUrl']

        _s3 = boto3.resource('s3')

        s3_bucket = template_url.split('/')[-2]
        s3_file = template_url.split('/')[-1]
        content_object = _s3.Object(s3_bucket, s3_file)
        file_content = content_object.get()['Body'].read().decode('utf-8')
        json_content = json.loads(file_content)
        template_full_path = f'/tmp/{sc_prod_name}.json'
        with open(template_full_path, 'w') as f:
            f.write(file_content)

        return template_full_path
    
    def post_processing(self, template_full_path: str):
        """
            Post processing of the CFN template
            post generation processing to replace account with dynamic reference for cross-account sharing

        Args:
            template_full_path (str): path of the CFN template

        Returns:
            [str]: path of the CFN template
        """
        processed_path = template_full_path.replace('.json', '_processed.json')
        with open(template_full_path, "r") as f:
            template = f.read()

        template = template.replace(f":{self.act_id}:", ":{{resolve:ssm:/mlops/dev/account_id}}:")
        template = template.replace(f":policy/{get_act_name_from_id(self.act_id)}-pol_PlatformUserBoundary", ":policy/{{resolve:ssm:/mlops/dev/account_name}}-pol_PlatformUserBoundary")

        cdk_bootstrap_bucket_name = f'cdk-hnb659fds-assets-{self.act_id}-{DEFAULT_DEPLOYMENT_REGION}'
        template = template.replace(cdk_bootstrap_bucket_name, get_code_bucket_name(self.act_id))

        json_template = json.loads(template)
        for item in json_template['Resources'].values():
            if item['Type'] == 'AWS::Lambda::Function':
                if 'Description' in item['Properties'] and 'AWS CDK resource provider framework - onEvent' in item['Properties']['Description']:
                    # cdk generate a dynamic guid for the custom resource provider, which breaks the service catalog stack,
                    # we replace it with a fixed name
                    item['Properties']['Code']['S3Key'] = 'custom-resource-provider-onevent.zip'
                continue

        template = json.dumps(json_template, indent=4)

        with open(processed_path, "w") as f:
            f.write(template)

        return processed_path