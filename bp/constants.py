from aws_cdk import (
    aws_ec2 as _ec2,
    Fn,
    CfnParameter
)
import boto3
import importlib

CODE_COMMIT_REPO_NAME = "ml-devops-sagemaker-studio-replica"

PIPELINE_ACCOUNT = "870955006425"
PIPELINE_ACCOUNT_NAME = "WS-0197"
PIPELINE_REGION = "eu-west-1"

# dev account can be identical to pipeline account as we deploy sc to pipeline account, and then share
# with all other. If we need to support different accounts, we need to bootstrap other account first, with
# a predefined role, i.e.:

# "AWS": "arn:aws:iam::387661743389:role/cdk-hnb659fds-deploy-role-387661743389-eu-west-1"
# as pipeline bucket is encrypted by KMS, KMS needs to allow the account decrypt the bucket. Without the pre-configured
# role in the destination account, KMS creation will fail.
DEV_ACCOUNT = "870955006425" 
DEV_ACCOUNT_NAME = "WS-0197"
DEV_REGION = "eu-west-1"

PREPROD_ACCOUNT = "870955006425"
PREPROD_ACCOUNT_NAME = "WS-0197"
PREPROD_REGION = "eu-west-1"

PROD_ACCOUNT = "870955006425"
PROD_ACCOUNT_NAME = "WS-0197"
PROD_REGION = "eu-west-1"


DEFAULT_DEPLOYMENT_REGION = "eu-west-1"
APP_PREFIX = "mlops"
sc_prod_launch_role_name = "MLOpsServiceCatalog-ProductLaunchRole"


CLIENT_DEV_ACCOUNT = '817207393703'
CLIENT_DEV_ACCOUNT_NAME = 'WS-00Z1'

STAGING_CLIENT_DEV_ACCOUNT = '976382856353'
STAGING_CLIENT_DEV_ACCOUNT_NAME = 'WS-00RI'

# CLIENT_DEV_ACCOUNT = '495986650785'
# CLIENT_DEV_ACCOUNT_NAME = 'WS-00Z5'

CLIENT_PREPROD_ACCOUNT = '923203785550'
STAGING_CLIENT_PREPROD_ACCOUNT = '142626708707'

FEATURE_DEV_ACCOUNT = '857181544807'
FEATURE_DEV_ACCOUNT_NAME = 'WS-00Z4'

FEATURE_GOV_ACCOUNT = '900292470358'
FEATURE_GOV_ACCOUNT_NAME = 'WS-00Z6'

CLIENT_PROD_ACCOUNT = '128426030628'
STAGING_CLIENT_PROD_ACCOUNT = 'TBD'

def get_client_dev_act_id(pipeline_type):
    if pipeline_type == 'DEV':
        return CLIENT_DEV_ACCOUNT
    elif pipeline_type == 'STAGING':
        return STAGING_CLIENT_DEV_ACCOUNT
    elif pipeline_type == 'FEATURE':
        return FEATURE_DEV_ACCOUNT
    else:
        assert False, f"Unknown pipeline type: {pipeline_type}"

def get_client_preprod_act_id(pipeline_type):
    if pipeline_type == 'DEV' or pipeline_type == 'FEATURE':
        return CLIENT_PREPROD_ACCOUNT
    elif pipeline_type == 'STAGING':
        return STAGING_CLIENT_PREPROD_ACCOUNT         
    else:
        assert False, f"Unknown pipeline type: {pipeline_type}"

def get_client_prod_act_id(pipeline_type):
    if pipeline_type == 'DEV' or pipeline_type == 'FEATURE':
        return CLIENT_PROD_ACCOUNT
    elif pipeline_type == 'STAGING':
        return STAGING_CLIENT_PROD_ACCOUNT         
    else:
        assert False, f"Unknown pipeline type: {pipeline_type}"

def get_act_name_from_id(act_id):
    '''
    return account name from account id
    '''
    if act_id == PIPELINE_ACCOUNT:
        return PIPELINE_ACCOUNT_NAME

    if act_id == STAGING_PIPELINE_ACCOUNT:
        return STAGING_PIPELINE_ACCOUNT_NAME

    if act_id == PROD_PIPELINE_ACCOUNT:
        return PROD_PIPELINE_ACCOUNT_NAME

    if act_id == DEV_ACCOUNT:
        return DEV_ACCOUNT_NAME

    if act_id == DEV_ACCOUNT:
        return DEV_ACCOUNT_NAME

    if act_id == FEATURE_DEV_ACCOUNT:
        return FEATURE_DEV_ACCOUNT_NAME

    if act_id == FEATURE_GOV_ACCOUNT:
        return FEATURE_GOV_ACCOUNT_NAME

    if act_id == PREPROD_ACCOUNT:
        assert False, 'TOBEIMPLEMENTED'

    if act_id == PROD_ACCOUNT:
        assert False, 'TOBEIMPLEMENTED'

    assert False, 'unknown account id'

STAGING_PIPELINE_ACCOUNT = "495986650785"
STAGING_PIPELINE_ACCOUNT_NAME = "WS-00Z5"

PROD_PIPELINE_ACCOUNT = "376571134915"
PROD_PIPELINE_ACCOUNT_NAME = "WS-01G6"

def get_code_bucket_name(act_id):
    assert act_id in [PIPELINE_ACCOUNT, STAGING_PIPELINE_ACCOUNT, PROD_PIPELINE_ACCOUNT, FEATURE_GOV_ACCOUNT]
    ret = f"ml-ops-shared-code-{act_id}"
    return ret

def get_vpc_info(the_stack):
    '''
    Get VPC info from the connected account so the lambda can be deployed in the same VPC
    '''

    bp_tags = {'aws:cloudformation:logical-id': 'ConnectedTgwVPC'}
    tgw_vpc = _ec2.Vpc.from_lookup(the_stack, 'my-vpc', region='eu-west-1', tags=bp_tags)

    app_subnet_ids = CfnParameter(
        the_stack, "subnet-ids", type="AWS::SSM::Parameter::Value<List<String>>",
        description="Account APP Subnets IDs", min_length=1,default="/vpc/subnets/private/ids").value_as_list
    subnets = [Fn.select(i, app_subnet_ids) for i in range(2)] # one lambda uses two subnets so works with region with 2 AZs
    subnets = _ec2.SubnetSelection(subnets=
            [_ec2.Subnet.from_subnet_attributes(the_stack, f'sn-{s}', subnet_id=s) for s in subnets]
        )

    sg_id = CfnParameter(the_stack,"sg-id",type="AWS::SSM::Parameter::Value<String>",
        description="Account Default Security Group id",min_length=1,default="/vpc/sg/id").value_as_string
    sg = _ec2.SecurityGroup.from_security_group_id(the_stack, 'sg', sg_id, mutable=False)
    return tgw_vpc,subnets,sg


def get_branch_info(the_stack):
    experiment_parent = CfnParameter(
        the_stack, 
        "experiment-parent", 
        type="String",
        description="Name of base project if the project is created for a branch", 
        default="None"
        ).value_as_string

    experiment_branch = CfnParameter(
        the_stack, 
        "experiment-branch", 
        type="String",
        description="Name of branch for the project", 
        default="main"
        ).value_as_string
    return experiment_parent, experiment_branch
    
def get_local_prod_version(templates_root, template_dir):
    '''
    get service catalog product version from local __version__.py file
    '''
    new_version = '0.0.0'
    from os import path
    version_path = path.join(templates_root, template_dir, '__version__.py')
    if path.exists(version_path):
        module = importlib.import_module(f'templates.{template_dir}.__version__')
        new_version = module.version
    
    return new_version

def get_sc_prod_version(prod_name):
    '''
    get the current version of the product from service catalog

    args:
        prod_name: the product name
    '''
    ret = '0.0.0'
    try:
        _sc = boto3.client('servicecatalog')
        prod_desc=_sc.describe_product(Name=prod_name)
        provisioning_artifact_id = prod_desc['ProvisioningArtifacts'][0]['Id']

        pa_desc = _sc.describe_provisioning_artifact(
            ProductId=prod_desc['ProductViewSummary']['ProductId'],
            ProvisioningArtifactId=provisioning_artifact_id
        )

        ret = pa_desc['ProvisioningArtifactDetail']['Name']

    except Exception as e:
        print(f"failed to get the product version of {prod_name}, use default version: 0.0.0, error: {e}")

    return ret
