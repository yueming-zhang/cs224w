import pytest
import os
import boto3
from mlops_sm_project_template_rt.config.constants import (
    CLIENT_PREPROD_ACCOUNT,
    CLIENT_PROD_ACCOUNT, 
    DEV_ACCOUNT, DEV_ACCOUNT_NAME, 
    CLIENT_DEV_ACCOUNT, CLIENT_DEV_ACCOUNT_NAME, 
    STAGING_CLIENT_DEV_ACCOUNT, STAGING_CLIENT_DEV_ACCOUNT_NAME, 
    STAGING_CLIENT_PREPROD_ACCOUNT,
    STAGING_CLIENT_PROD_ACCOUNT,
    PIPELINE_ACCOUNT, PIPELINE_ACCOUNT_NAME,
    STAGING_PIPELINE_ACCOUNT, STAGING_PIPELINE_ACCOUNT_NAME,
    FEATURE_DEV_ACCOUNT, FEATURE_DEV_ACCOUNT_NAME,
    FEATURE_GOV_ACCOUNT, FEATURE_GOV_ACCOUNT_NAME,
    get_client_preprod_act_id,
    get_client_prod_act_id
)
from unittest.mock import patch

def pytest_addoption(parser):
    parser.addoption("--targetact", action='store', default='DEV')
    # parser.addoption("--targetact", action='store', default='STAGING')


@pytest.fixture
def targetact_arg(pytestconfig):
    return pytestconfig.getoption("targetact")

def get_pipeline_act(target_arg):
    if target_arg == 'DEV':
        return PIPELINE_ACCOUNT
    elif target_arg == 'FEAT':
        return FEATURE_GOV_ACCOUNT
    elif target_arg == 'STAGING':
        return STAGING_PIPELINE_ACCOUNT
    elif target_arg == 'PROD':
        assert False, "PROD pipeline type not implemented"
    else:
        raise Exception(f"Unknown pipeline type: {target_arg}")

def get_pipeline_name(target_arg):
    if target_arg == 'DEV':
        return PIPELINE_ACCOUNT_NAME
    if target_arg == 'FEAT':
        return FEATURE_GOV_ACCOUNT_NAME
    elif target_arg == 'STAGING':
        return STAGING_PIPELINE_ACCOUNT_NAME
    elif target_arg == 'PROD':
        assert False, "PROD pipeline type not implemented"
    else:
        raise Exception(f"Unknown pipeline type: {target_arg}")

def get_client_dev_act(target_arg):
    if target_arg == 'DEV':
        return CLIENT_DEV_ACCOUNT
    if target_arg == 'FEAT':
        return FEATURE_DEV_ACCOUNT
    elif target_arg == 'STAGING':
        return STAGING_CLIENT_DEV_ACCOUNT
    else:
        raise Exception(f"We do not support test on : {target_arg}")

def get_client_dev_act_name(target_arg):
    if target_arg == 'DEV':
        return CLIENT_DEV_ACCOUNT_NAME
    if target_arg == 'FEAT':
        return FEATURE_DEV_ACCOUNT_NAME
    elif target_arg == 'STAGING':
        return STAGING_CLIENT_DEV_ACCOUNT_NAME
    else:
        raise Exception(f"We do not support test on : {target_arg}")


@pytest.fixture
def client_dev_env(monkeypatch, targetact_arg):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("ACCOUNT_ID", get_client_dev_act(targetact_arg))  
    monkeypatch.setenv("PIPELINE_ACCOUNT_ID", get_pipeline_act(targetact_arg))  
    if os.environ.get("CODEBUILD_BUILD_IMAGE", None) is None:
        monkeypatch.setenv("AWS_PROFILE", f"{get_client_dev_act_name(targetact_arg)}-role_AUTOMATION")
        monkeypatch.setenv("AWS_REGION", "eu-west-1")  

@pytest.fixture
def client_dev_admin_env(monkeypatch, targetact_arg):
    monkeypatch.setenv("AWS_PROFILE", f"{get_client_dev_act_name(targetact_arg)}-role_IAM-ADM")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")    


@pytest.fixture
def mgmt_dev_env(monkeypatch, targetact_arg):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("ACCOUNT_ID", get_pipeline_act(targetact_arg))  
    if os.environ.get("CODEBUILD_BUILD_IMAGE", None) is None:
        monkeypatch.setenv("AWS_PROFILE", f"{get_pipeline_name(targetact_arg)}-role_AUTOMATION")
        monkeypatch.setenv("AWS_REGION", "eu-west-1")  

    monkeypatch.setenv("CLIENT_DEV_ACCOUNT", get_client_dev_act(targetact_arg))
    monkeypatch.setenv("CLIENT_PREPROD_ACCOUNT", get_client_preprod_act_id(targetact_arg))
    monkeypatch.setenv("CLIENT_PROD_ACCOUNT", get_client_prod_act_id(targetact_arg))

@pytest.fixture
def mgmt_staging_env(monkeypatch, targetact_arg):
    targetact_arg = 'STAGING'
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("ACCOUNT_ID", get_pipeline_act(targetact_arg))  
    if os.environ.get("CODEBUILD_BUILD_IMAGE", None) is None:
        monkeypatch.setenv("AWS_PROFILE", f"{get_pipeline_name(targetact_arg)}-role_AUTOMATION")
        monkeypatch.setenv("AWS_REGION", "eu-west-1")  

    monkeypatch.setenv("CLIENT_DEV_ACCOUNT", get_client_dev_act(targetact_arg))
    monkeypatch.setenv("CLIENT_PREPROD_ACCOUNT", get_client_preprod_act_id(targetact_arg))
    monkeypatch.setenv("CLIENT_PROD_ACCOUNT", get_client_prod_act_id(targetact_arg))


@pytest.fixture
def mgmt_dev_admin_env(monkeypatch, targetact_arg):
    monkeypatch.setenv("AWS_PROFILE", f"{get_pipeline_name(targetact_arg)}-role_IAM-ADM")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")    


@pytest.fixture
def client_automation_role(mgmt_dev_env, targetact_arg):
    '''
    from management account assume the client automation role, so can run tests against client account
    '''

    sts_client = boto3.client('sts')
    assumed_role_obj = sts_client.assume_role(
        RoleArn=f'arn:aws:iam::{get_client_dev_act(targetact_arg)}:role/bootstrap-from-mgmt',
        RoleSessionName='AssumeRoleSession1',
        DurationSeconds=60*60 # 1 hour
    )
    assumed_credential = assumed_role_obj['Credentials']

    myboto3client = boto3.client

    def mocked_boto3_client(client_type: str, region_name="eu-west-1"):
        ret = myboto3client(client_type,
                            aws_access_key_id=assumed_credential['AccessKeyId'],
                            aws_secret_access_key=assumed_credential['SecretAccessKey'],
                            aws_session_token=assumed_credential['SessionToken']
                            )
        return ret

    with patch('boto3.client', new=mocked_boto3_client):
        yield mocked_boto3_client

