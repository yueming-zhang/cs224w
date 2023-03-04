import aws_cdk as cdk
from unittest.mock import patch
from pathlib import Path
from mlops_sm_project_template_rt.config.constants import (
    get_local_prod_version,
    get_sc_prod_version
)
from mlops_sm_project_template_rt.pipeline_stack import CoreStage, ServiceCatalogStack
from mlops_sm_project_template_rt.config.constants import DEFAULT_DEPLOYMENT_REGION, PIPELINE_ACCOUNT, STAGING_PIPELINE_ACCOUNT
from aws_cdk import assertions
import json
import zipfile
import os
from pathlib import Path

template_name = 'Arima'
template_root = f"{Path(__file__).parents[2]}/templates"
def test_get_local_sc_version():
    """
    verify the Service Catalog portfilio is deployed to the dev account
    """
    version = get_local_prod_version(template_root, template_name)
    
    # read the version from the local file: 
    version_file = f"{template_root}/{template_name}/__version__.py"
    with open(version_file, 'r') as f:
        version_content = f.read()

    assert f'''version = "{version}"''' in version_content

def test_no_version_file():

    # mock path.exists to return False, then call get_local_prod_version
    with patch('os.path.exists') as mock_exists:
        mock_exists.return_value = False
        version = get_local_prod_version(template_root, template_name)

    assert version == '0.0.1'

def test_newer_version_available():

    # mock get_local_prod_version to return 5.0
    # mock get_sc_prod_version to return 1.0

    pipeline_env = cdk.Environment(account=PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
    stage = cdk.App()

    new_version = '5.1.2'
    with patch('mlops_sm_project_template_rt.config.constants.get_local_prod_version') as mock_local:
        mock_local.return_value = new_version
        with patch('mlops_sm_project_template_rt.config.constants.get_sc_prod_version') as mock_sc:
            mock_sc.return_value = '1.0'
            stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)

    template = assertions.Template.from_stack(stack)        
    res = template.to_json()['Resources']

    the_stack = [res[sc] for sc in res if res[sc]['Type'] == 'AWS::ServiceCatalog::CloudFormationProduct' and res[sc]['Properties']['Name']==template_name][0]
    assert the_stack['Properties']['ProvisioningArtifactParameters'][0]['Name'] == new_version


    generated_template_path = stack.get_generated_template(template_name)

    res = json.loads(open(generated_template_path).read())['Resources']

    versions = [res[sc]['Properties']['Tags'][2]['Value'] for sc in res if 'Tags' in res[sc]['Properties']]
    for v in versions:
        assert v == new_version

    pass


def test_newer_version_unavailable_dev():

    '''
    verify that when no newer version but the stack should still be deployed because it is in dev
    '''

    pipeline_env = cdk.Environment(account=PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
    stage = cdk.App()

    new_version = '0.1.2'
    cur_version = '1.2.7'
    with patch('mlops_sm_project_template_rt.config.constants.get_local_prod_version') as mock_local:
        mock_local.return_value = new_version
        with patch('mlops_sm_project_template_rt.config.constants.get_sc_prod_version') as mock_sc:
            mock_sc.return_value = cur_version
            stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)

    template = assertions.Template.from_stack(stack)        
    res = template.to_json()['Resources']

    the_stack = [res[sc] for sc in res if res[sc]['Type'] == 'AWS::ServiceCatalog::CloudFormationProduct' and res[sc]['Properties']['Name']==template_name][0]
    assert the_stack['Properties']['ProvisioningArtifactParameters'][0]['Name'] == new_version

    generated_template_path = stack.get_generated_template(template_name)

    res = json.loads(open(generated_template_path).read())['Resources']

    versions = [res[sc]['Properties']['Tags'][2]['Value'] for sc in res if 'Tags' in res[sc]['Properties']]
    for v in versions:
        assert v == new_version

    pass


def test_nothing_ready_to_release():

    '''
    verify that when no version >= 1.0, nothing should be released to staging
    '''

    pipeline_env = cdk.Environment(account=STAGING_PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)
    stage = cdk.App()

    new_version = '0.1.2'
    cur_version = '1.2.7'
    with patch('mlops_sm_project_template_rt.config.constants.get_local_prod_version') as mock_local:
        mock_local.return_value = new_version
        with patch('mlops_sm_project_template_rt.config.constants.get_sc_prod_version') as mock_sc:
            mock_sc.return_value = cur_version
            stack = ServiceCatalogStack(stage, "MLOpsServiceCatalog", env=pipeline_env)

    template = assertions.Template.from_stack(stack)        
    res = template.to_json()['Resources']

    the_stack = [res[sc] for sc in res if res[sc]['Type'] == 'AWS::ServiceCatalog::CloudFormationProduct' and res[sc]['Properties']['Name']==template_name]
    assert len(the_stack) == 0

def test_code_stack_dev():
    '''
    when on dev, regardless newer version is available, the code stack use the local version
    '''
    pipeline_env = cdk.Environment(account=PIPELINE_ACCOUNT, region=DEFAULT_DEPLOYMENT_REGION)

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


