"""
these tests verify:
- the service catalog is correctly deployed
- the service catalog contains sagemaker studio project template

"""

import pytest
import boto3, botocore
from botocore.client import ClientError
from unittest.mock import patch
from datetime import datetime

import sys, os, inspect, json, time

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))  # type: ignore
parentdir = os.path.dirname(os.path.dirname(currentdir))
sys.path.insert(0, parentdir)
root = os.path.dirname(parentdir)
sys.path.insert(0, root)

os.chdir(parentdir)
from mlops_sm_project_template_rt.config.constants import (
    DEV_ACCOUNT,
    get_code_bucket_name,
    CLIENT_DEV_ACCOUNT,
)

from utils.shared import wait_for_pipeline

pp_name = "autotest-prj1"


@pytest.mark.skip("to be completed")
def test_assume_launch_role(mgmt_dev_env):
    """
    verify that the production launch role allow be assumed from governance account
    """
    role_arn = f"arn:aws:iam::{DEV_ACCOUNT}:role/MLOpsServiceCatalog-ProductLaunchRole"

    sts_client = boto3.client("sts")
    assumed_role_obj = sts_client.assume_role(
        RoleArn=role_arn, RoleSessionName="session1"
    )
    assumed_credential = assumed_role_obj["Credentials"]
    assert assumed_credential["AccessKeyId"] is not None


def test_sgmkr_service_catalog_portfilio(mgmt_dev_env):
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


def test_service_catalog_visible_to_sgmkr_studio(mgmt_dev_env):
    """
    verify at least one service catalog proudct is visible to sagemaker studio
    """
    client = boto3.client("servicecatalog")
    prod = client.describe_product_as_admin(Name="Abalone")

    assert prod is not None
    prod_arn = prod["ProductViewDetail"]["ProductARN"]
    print(f"{prod_arn=}", flush=True)

    sgmkr_tags = [t for t in prod["Tags"] if t["Key"] == "sagemaker:studio-visibility"]
    assert len(sgmkr_tags) >= 1
    pass


def test_mgmt_provision_service_catalog(mgmt_dev_env):
    """
    provision the service catalog, and verify:
    - the cfn stack created successfully
    - the build pipeline (training job) runs successfully

    note: need to associate with an IAM Principal before create the stack

    """
    print(
        f"--------------------------------------------------------------------------------"
    )
    print(f"1. test provision service catalog", flush=True)
    caller_id = boto3.client("sts").get_caller_identity()
    caller_arn = caller_id.get("Arn")
    role = caller_arn.split("/")[1]
    act_id = caller_id.get("Account")
    caller_role_arn = f"arn:aws:iam::{act_id}:role/{role}"

    print(f"2. {datetime.now()}: delete provisioned service catalog {pp_name}", flush=True)
    status = delete_provisioned_sc(pp_name, act_id)

    print(f"3. {datetime.now()}: delete {pp_name} complete, status = {status}", flush=True)
    sc_client = boto3.client("servicecatalog")

    portfolios = sc_client.list_portfolios()
    pf_list = [
        f
        for f in portfolios["PortfolioDetails"]
        if f["DisplayName"] == "SageMaker Organization Templates"
    ]
    assert len(pf_list) == 1
    portfolio_id = pf_list[0]["Id"]

    print(
        f"4. {datetime.now()}: associating principal {caller_role_arn} to portfolio {portfolio_id}",
        flush=True,
    )
    sc_prod, path_id = associate_principal(portfolio_id, caller_role_arn)
    prod_id = sc_prod["ProductViewSummary"]["ProductId"]

    print(f"5. {datetime.now()}: start provision product {pp_name}", flush=True)
    ret = sc_client.provision_product(
        ProductId=prod_id,
        ProvisionedProductName=pp_name,
        ProvisioningArtifactId=sc_prod["ProvisioningArtifacts"][0]["Id"],
        PathId=path_id,
        ProvisioningParameters=[
            {"Key": "SageMakerProjectId", "Value": pp_name},
            {"Key": "SageMakerProjectName", "Value": pp_name},
        ],
    )
    assert ret["RecordDetail"]["ProvisionedProductName"] == pp_name
    assert ret["RecordDetail"]["ProvisionedProductType"] == "CFN_STACK"
    pp_id = ret["RecordDetail"]["ProvisionedProductId"]

    cfn_name = f"SC-{act_id}-{pp_id}"
    status = wait_for_final_cfn_status(cfn_name)
    print(
        f"6. {datetime.now()}: cloudformation provision complete: ProvisionedProductId = {pp_id}, status = {status}",
        flush=True,
    )
    assert status in [
        "CREATE_COMPLETE",
        "UPDATE_COMPLETE",
        "IMPORT_COMPLETE",
    ], "cfn creation completed"

    status = wait_for_pipeline(f'{pp_name}-master', stage_name='TrainPipeline') #continue when train pipeline is deployed
    assert status == 'Succeeded', 'master pipeline job completed'

    status = wait_for_pipeline(f'{pp_name}-train', stage_name='RunSMPipeline')
    print(f"7. {datetime.now()}: initial training job pipeline complete: status = {status}", flush=True)
    if status != "Succeeded":
        # the first time run is triggered by cfn provisioning, may fail as permissions not set yet, so retry
        time.sleep(20)
        pipeline_client = boto3.client("codepipeline")
        pipeline_client.start_pipeline_execution(name=f"{pp_name}-train")
        status = wait_for_pipeline(f"{pp_name}-train")
        print(
            f"8. {datetime.now()}: second training job pipeline complete: status = {status}", flush=True
        )

    assert status == "Succeeded", "training job completed"

    # verify that the model package is created
    mgp_name = f"{pp_name}-model-group"
    sgmkr_client = boto3.client("sagemaker")
    pkgs = sgmkr_client.list_model_packages(ModelPackageGroupName=mgp_name)
    assert len(pkgs["ModelPackageSummaryList"]) == 1, "model package created"
    pkg = sgmkr_client.describe_model_package(
        ModelPackageName=pkgs["ModelPackageSummaryList"][0]["ModelPackageArn"]
    )
    assert (
        pkg["ModelApprovalStatus"] == "PendingManualApproval"
    ), "model package is approved"
    print(
        f"9. {datetime.now()}: verified mpg {mgp_name} is created and is in PendingManualApproval",
        flush=True,
    )


def test_client_access_seeds3(client_dev_env):
    """
    verify that client account can access the seed repo in s3 in magmt account
    """
    # create a bucket in magmt account
    s3_client = boto3.client("s3")
    file_name = "lambda_cleanup_code.zip"

    pipeline_account_id = os.environ.get("PIPELINE_ACCOUNT_ID")

    ret = s3_client.head_object(
        Bucket=get_code_bucket_name(pipeline_account_id), Key=file_name
    )
    assert ret["ContentLength"] > 5000

    with open("/tmp/delete-me.zip", "wb") as f:
        s3_client.download_fileobj(
            get_code_bucket_name(pipeline_account_id), file_name, f
        )
    pass


# @pytest.mark.skip('to be completed')
def test_service_catalog_share(mgmt_dev_env):
    """
    test the service catalog share
    """
    client = boto3.client("servicecatalog")

    portfolios = client.list_portfolios()
    pf_list = [
        f
        for f in portfolios["PortfolioDetails"]
        if f["DisplayName"] == "SageMaker Organization Templates"
    ]

    assert len(pf_list) == 1

    ret = client.create_portfolio_share(
        PortfolioId=pf_list[0]["Id"], AccountId=CLIENT_DEV_ACCOUNT, ShareTagOptions=True
    )

    assert ret["ResponseMetadata"]["HTTPStatusCode"] == 200
    pass


def wait_for_final_cfn_status(cfn_name, _cfn_client=None):
    """
    wait until the cfn reach final status

    """
    print(f"   - {datetime.now()}: waiting for cfn {cfn_name} to complete", flush=True)
    if _cfn_client is None:
        _cfn_client = boto3.client("cloudformation")

    try:
        while True:
            time.sleep(10)
            res = _cfn_client.describe_stacks(StackName=cfn_name)
            if not res["Stacks"][0]["StackStatus"].endswith("_IN_PROGRESS"):
                break
        ret = res["Stacks"][0]["StackStatus"]
    except ClientError as e:
        print(f"   - {datetime.now()} : cfn {cfn_name} exception = {e}", flush=True)
        ret = "DELETE_COMPLETE"  # Stack does not exist

    print(f"   - {datetime.now()} : cfn {cfn_name} status = {ret}", flush=True)

    return ret


def delete_provisioned_sc(pp_name, act_id):
    """
    delete the already provisioned service catalog, so we can repeat the
    test
    """
    sc_client = boto3.client("servicecatalog")
    try:
        desc = sc_client.describe_provisioned_product(Name=pp_name)
    except sc_client.exceptions.ResourceNotFoundException as e:
        print(f"   - {pp_name} does not exist, skip deletion, {e}", flush=True)
        time.sleep(60) #child stack deletion takes time
        assert_child_stacks_deleted(pp_name)
        return "DELETE_COMPLETE"

    ret = sc_client.terminate_provisioned_product(
        ProvisionedProductName=pp_name, IgnoreErrors=True
    )

    # wait for deletion completion
    prod_id = desc["ProvisionedProductDetail"]["Id"]
    cfn_name = f"SC-{act_id}-{prod_id}"

    status = wait_for_final_cfn_status(cfn_name)
    assert status.endswith(
        "DELETE_COMPLETE"
    ), f"cfn deletion completed, actual = {status}"

    sleeped_duration = 0
    while True and sleeped_duration < 60:
        try:
            desc = sc_client.describe_provisioned_product(Name=pp_name)
            # desc['ProvisionedProductDetail']['Status'] == 'UNDER_CHANGE'
        except sc_client.exceptions.ResourceNotFoundException:
            time.sleep(60) #child stack deletion takes time
            assert_child_stacks_deleted(pp_name=pp_name)
            return "DELETE_COMPLETE"
        time.sleep(10)
        sleeped_duration += 10

    assert False, "provisioned sc is not deleted"

def assert_child_stacks_deleted(pp_name=pp_name):
    '''
    assert that all child pipeline stacks are deleted
    '''

    cfn_client = boto3.client("cloudformation")
    children = ['build', 'train', 'deploy']
    for child in children:
        stack_name = f'{pp_name}-{child}-pipeline'
        print (f'asserting {stack_name} is deleted')
        with pytest.raises(ClientError) as e:
            cfn_client.describe_stacks(StackName=stack_name)

        err = e.value.response['Error']
        assert err['Code'] == 'ValidationError', f'child stack {stack_name} is deleted' 
        assert err['Message'] == f'Stack with id {stack_name} does not exist'


@pytest.mark.skip("to be completed")
def test_delete_model_package_groups(client_dev_env):
    mpg_names = ["atarima-train-mpg", 
                 "atarima-train-mpg-2nd-Execution-StdArima",
                 "atarima-train-mpg-StdArima",
                 "atarima-one-mpg-autotest",
                 "atarima-one-mpg-2nd-Execution-StdArima",
                 "atarima-one-mpg-StdArima",
                 "atarima-two-mpg",
                 "atarima-atarima",
                 "atarima-one-mpg-StdArima-20230221093729",
                 "atarima-one-mpg-2nd-Execution-StdArima-20230221093736",
                 "atarima-one-mpg-atarima-one-StdArima"
                 ]
    for mpg_name in mpg_names:
        try:
            delete_model_packages(mpg_name, False)
            sgmkr_client = boto3.client("sagemaker")
            sgmkr_client.delete_model_package_group(ModelPackageGroupName=mpg_name)
        except Exception as e:
            pass
    pass


def delete_model_packages(pp_name, append_model_group_name=True):
    """
    delete model package in the model package group so that the MPG can be
    deleted as part of cfn deletion, otherwise cnf deletion will fail

    """
    mgp_name = f"{pp_name}-model-group" if append_model_group_name else pp_name
    sgmkr_client = boto3.client("sagemaker")
    print(
        f"   - delete all model packages from Model Package Group: {mgp_name}",
    )

    pkgs = sgmkr_client.list_model_packages(ModelPackageGroupName=mgp_name)
    pkgs = []
    extra_args = {}
    while True:
        res = sgmkr_client.list_model_packages(
            ModelPackageGroupName=mgp_name, **extra_args
        )
        pkgs += res["ModelPackageSummaryList"]

        if "NextToken" in res:
            extra_args["NextToken"] = res["NextToken"]
        else:
            break

    for pkg in pkgs:
        ret = sgmkr_client.delete_model_package(ModelPackageName=pkg["ModelPackageArn"])
        pass

    return


def delete_endpoint_cfn(cfn_name, client=None):
    if client is None:
        client = boto3.client("cloudformation")
    client.delete_stack(StackName=cfn_name)
    ret = wait_for_final_cfn_status(cfn_name, client)

    assert ret == "DELETE_COMPLETE", "cfn deletion completed"


def stop_deployment_pipeline(pp_name = pp_name):
    try:
        _pl = boto3.client("codepipeline")
        pl_state = _pl.get_pipeline_state(name=f"{pp_name}-deploy")
        latest_actions = [
            item for item in pl_state["stageStates"] if "latestExecution" in item
        ][-1]
        # latest_action = [a for a in latest_actions['actionStates'] if 'latestExecution' in a][-1]
        e_id = latest_actions["latestExecution"]["pipelineExecutionId"]
        if not latest_actions["latestExecution"]["status"].endswith("ed"):
            ret = _pl.stop_pipeline_execution(
                pipelineName=f"{pp_name}-deploy", pipelineExecutionId=e_id, abandon=True
            )
            time.sleep(5)
    except Exception as e:
        pass
    return


def associate_principal(portfolio_id, caller_role_arn, pp_name='Abalone'):
    """
    associate the caller role to the service catalog product
    """
    sc_client = boto3.client("servicecatalog")

    ret = sc_client.associate_principal_with_portfolio(
        PortfolioId=portfolio_id, PrincipalType="IAM", PrincipalARN=caller_role_arn
    )
    time.sleep(10)

    if 'fna' in pp_name:
        template_name = 'FNA'
    elif 'arima' in pp_name:
        template_name = 'Arima'
    else:
        template_name = 'Abalone'
    sc_prod = sc_client.describe_product(Name=template_name)
    assert sc_prod is not None
    assert len(sc_prod["ProvisioningArtifacts"]) == 1
    prod_id = sc_prod["ProductViewSummary"]["ProductId"]

    paginator = sc_client.get_paginator("list_launch_paths")
    response_iterator = paginator.paginate(ProductId=prod_id)

    path_list = [item for item in response_iterator]
    assert len(path_list) > 0
    assert len(path_list[0]["LaunchPathSummaries"]) > 0
    path_id = path_list[0]["LaunchPathSummaries"][0]["Id"]

    return sc_prod, path_id


def verify_batch_transform(sm_client=None, pp_name=pp_name, pipeline_name=None):
    """
    GIVEN:  a model is trained with acceptable performance, and the model is registerred
    WHEN:   the model is approved in SageMaker Studio
    THEN:   the batch transform sagemaker pipeline exists, and runs successfully
    """

    _sm = boto3.client("sagemaker") if sm_client is None else sm_client
    pipelines = _sm.list_pipelines()

    pipeline_name = f"{pp_name}-transform" if pipeline_name is None else pipeline_name
    print(f"   - verify batch transform pipeline: {pipeline_name}", flush=True)
    transform_pipeline = [
        p for p in pipelines["PipelineSummaries"] if p["PipelineName"] == pipeline_name
    ]
    assert len(transform_pipeline) == 1
    desc = _sm.describe_pipeline(PipelineName=transform_pipeline[0]["PipelineName"])
    if desc["PipelineStatus"] == "Active":
        print(f"     - start batch transform pipeline: {pipeline_name}", flush=True)
        ret = _sm.start_pipeline_execution(
            PipelineName=transform_pipeline[0]["PipelineName"]
        )
        # wait for the pipeline to complete
        while True:
            desc = _sm.describe_pipeline_execution(
                PipelineExecutionArn=ret["PipelineExecutionArn"]
            )
            if desc["PipelineExecutionStatus"] in ["Executing", "Stopping"]:
                time.sleep(10)
            else:
                break
        print(
            f"     - pipeline: {pipeline_name} result = {desc['PipelineExecutionStatus']}",
            flush=True,
        )

        if desc["PipelineExecutionStatus"] != "Succeeded":
            step_desc = _sm.list_pipeline_execution_steps(
                PipelineExecutionArn=ret["PipelineExecutionArn"]
            )

        assert desc["PipelineExecutionStatus"] == "Succeeded"

        return ret
    return None


@pytest.mark.skip("local only")
def test_codepipeline_stack_creation(client_dev_env):
    from lambda_code.lambda_cleanup_code.index import handler

    payload = {
        "RequestType": "Create",
        "ServiceToken": "arn:aws:lambda:eu-west-1:870955006425:function:SC-870955006425-pp-3l5dco-cleanupfuncproviderframe-KEiha2W9sEVt",
        "ResponseURL": "https://cloudformation-custom-resource-response-euwest1.s3-eu-west-1.amazonaws.com/arn%3Aaws%3Acloudformation%3Aeu-west-1%3A870955006425%3Astack/SC-870955006425-pp-3l5dco234jq3m/31c0e3e0-2d28-11ed-ad1b-02cb7fa9382b%7Ccleanupfunccustomresource%7C02ae23ee-5969-4b84-8b30-6fbfdc074cf0?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20220905T143945Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7199&X-Amz-Credential=AKIAU7SEXKRM3ONM67EX%2F20220905%2Feu-west-1%2Fs3%2Faws4_request&X-Amz-Signature=1130e58e561cebb851fd707339950a6321d338e77640865ecc41c4bc04492c51",
        "StackId": "arn:aws:cloudformation:eu-west-1:870955006425:stack/SC-870955006425-pp-3l5dco234jq3m/31c0e3e0-2d28-11ed-ad1b-02cb7fa9382b",
        "RequestId": "02ae23ee-5969-4b84-8b30-6fbfdc074cf0",
        "LogicalResourceId": "cleanupfunccustomresource",
        "ResourceType": "Custom::CleanupSageMakerProject",
        "ResourceProperties": {
            "ServiceToken": "arn:aws:lambda:eu-west-1:870955006425:function:SC-870955006425-pp-3l5dco-cleanupfuncproviderframe-KEiha2W9sEVt",
            "prod_account_id": ":870955006425:",
            "staging_account_id": ":870955006425:",
            "bucket_names": [
                "mlops-autotest-prj1-eu-west-1-870955006425",
                "pipeline-autotest-prj1-eu-west-1-870955006425",
            ],
            "project_name": "autotest-prj1",
            "mpg_name": "autotest-prj1-model-group",
            "template_name": "Abalone",
            "shared_code_s3_bucket_name": "ml-ops-shared-code-870955006425",
            "deploy_app_key": "Abalone-deploy_app.zip",
            "project_id": "autotest-prj1"
        },
    }

    handler(payload, None)

    pass

@pytest.mark.skip("local only")
def test_codepipeline_stack_deletion(client_dev_env):
    from lambda_code.lambda_cleanup_code.index import handler

    payload = {
        "RequestType": "Delete",
        "ServiceToken": "arn:aws:lambda:eu-west-1:870955006425:function:SC-870955006425-pp-3l5dco-cleanupfuncproviderframe-KEiha2W9sEVt",
        "ResponseURL": "https://cloudformation-custom-resource-response-euwest1.s3-eu-west-1.amazonaws.com/arn%3Aaws%3Acloudformation%3Aeu-west-1%3A870955006425%3Astack/SC-870955006425-pp-3l5dco234jq3m/31c0e3e0-2d28-11ed-ad1b-02cb7fa9382b%7Ccleanupfunccustomresource%7C02ae23ee-5969-4b84-8b30-6fbfdc074cf0?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=20220905T143945Z&X-Amz-SignedHeaders=host&X-Amz-Expires=7199&X-Amz-Credential=AKIAU7SEXKRM3ONM67EX%2F20220905%2Feu-west-1%2Fs3%2Faws4_request&X-Amz-Signature=1130e58e561cebb851fd707339950a6321d338e77640865ecc41c4bc04492c51",
        "StackId": "arn:aws:cloudformation:eu-west-1:870955006425:stack/SC-870955006425-pp-3l5dco234jq3m/31c0e3e0-2d28-11ed-ad1b-02cb7fa9382b",
        "RequestId": "02ae23ee-5969-4b84-8b30-6fbfdc074cf0",
        "LogicalResourceId": "cleanupfunccustomresource",
        "ResourceType": "Custom::CleanupSageMakerProject",
        "ResourceProperties": {
            "ServiceToken": "arn:aws:lambda:eu-west-1:870955006425:function:SC-870955006425-pp-3l5dco-cleanupfuncproviderframe-KEiha2W9sEVt",
            "prod_account_id": ":870955006425:",
            "staging_account_id": ":870955006425:",
            "bucket_names": [
                "mlops-autotest-prj1-eu-west-1-870955006425",
                "pipeline-autotest-prj1-eu-west-1-870955006425",
            ],
            "project_name": "autotest-prj1",
            "mpg_name": "autotest-prj1-model-group",
            "template_name": "Abalone",
            "shared_code_s3_bucket_name": "ml-ops-shared-code-870955006425",
            "deploy_app_key": "Abalone-deploy_app.zip",
            "project_id": "autotest-prj1"
        },
    }

    handler(payload, None)

    pass