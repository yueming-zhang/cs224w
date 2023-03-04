'''
To deploy code into S3 as zip file, so that it can be used by Lambdas in the same account,
and also by Lambdas in other accounts when Service Catalog is launched from other accounts.
'''


from aws_cdk import (
    aws_s3 as _s3,
    aws_s3_deployment as s3_deployment,
    Stack
)
import aws_cdk
import shutil
from os import path, listdir

from constructs import Construct
from pathlib import Path

from mlops_sm_project_template_rt.permission_boundary import PermissionBoundaryAspect
from mlops_sm_project_template_rt.config.constants import (
    PIPELINE_ACCOUNT,
    FEATURE_DEV_ACCOUNT,
    get_code_bucket_name, 
    get_sc_prod_version, 
    get_local_prod_version
)

class SharedCodeStack(Stack):
    """
    Pipeline Stack
    Pipeline stack which provisions code pipeline for CICD deployments for the project resources.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        # cloud_assembly_artifact: codepipeline.Artifact,
        **kwargs,
    ):
        super().__init__(scope, id, **kwargs)
        self.act_id = kwargs['env'].account
        assert len(self.act_id) == 12

        code_bucket = _s3.Bucket(self, "MLOpsSharedCodeBucket",
            bucket_name = get_code_bucket_name(self.act_id),
            removal_policy=aws_cdk.RemovalPolicy.DESTROY)
        
        # below create a path relative to the current file, to make sure cli and pytest vscode all able to 
        # find the file
        root_dir = str(Path(__file__).parents[1])
        lambda_code_dir = f'{root_dir}/lambda_code'

        zips = []
        for subdir in listdir(lambda_code_dir):
            if path.isdir(path.join(lambda_code_dir, subdir)):
                ret = self.create_zip_in_s3(lambda_code_dir, subdir)
                zips.append(ret)

        root_dir_templates = f'{root_dir}/templates' # search for */template_name/seed_code sub folders
        self.zips_app = []
        for template_dir in listdir(root_dir_templates):
            template_root = path.join(root_dir_templates, template_dir)
            if not path.isdir(template_root):
                continue
            if not path.isfile(path.join(template_root, f'{template_dir}Stack.py')):
                continue
            for seeddir in listdir(template_root):
                if seeddir != 'seed_code':
                    continue
                seed_code_dir = path.join(root_dir_templates, template_dir, seeddir)
                cur_version = get_sc_prod_version(template_dir)
                new_version = get_local_prod_version(root_dir_templates, template_dir)
                for subdir in listdir(seed_code_dir):
                    if path.isfile(path.join(template_root, '__version__.py')):
                        shutil.copy(path.join(template_root, '__version__.py'), path.join(seed_code_dir, subdir))

                    if self.act_id in [PIPELINE_ACCOUNT, FEATURE_DEV_ACCOUNT]:
                        ret = self.create_zip_in_s3(seed_code_dir, subdir, prefix=template_dir)
                    elif new_version <= cur_version:
                        ret = self.load_zip_from_s3(seed_code_dir, subdir, prefix=template_dir)
                    else:
                        ret = self.create_zip_in_s3(seed_code_dir, subdir, prefix=template_dir)
                    self.zips_app.append(ret)

        code_zip = s3_deployment.BucketDeployment(self, id=f"{subdir}",
                                                  destination_bucket=code_bucket,
                                                  sources=[s3_deployment.Source.asset(path=p) for p in zips + self.zips_app])

        pass

    def create_zip_in_s3(self, root_dir, subdir, prefix=''):
        '''
        zip the file twice as Cdk always unzip it when upload, while lambda requires a zip file
        so after one unzip, the zip remains. 
        '''
        root_dir = path.join(root_dir, subdir)

        fn = f'{prefix}-{subdir}' if len(prefix)> 0 else subdir
        zip_file_path = shutil.make_archive(base_name=f'tmp/{fn}',
                            format='zip',
                            root_dir=root_dir,
                            base_dir=None)
        zip_file_path1 = shutil.make_archive(base_name=f'tmp/{prefix}{subdir}-tmp',
                            format='zip',
                            root_dir=path.dirname(zip_file_path),
                            base_dir=f'{fn}.zip')   
        return zip_file_path1
    
    def load_zip_from_s3(self, root_dir, subdir, prefix=''):
        '''
        if no newer version available, use the already deployed version
        '''
        assert len(prefix) > 0
        key = f'{prefix}-{subdir}.zip'

        #download the zip file from s3
        import boto3
        s3 = boto3.resource('s3')
        zip_file_path = f'tmp/{key}'
        s3.Bucket(get_code_bucket_name(self.act_id)).download_file(key, zip_file_path)
      
        zip_file_path1 = shutil.make_archive(base_name=f'tmp/{prefix}{subdir}-tmp',
                            format='zip',
                            root_dir=path.dirname(zip_file_path),
                            base_dir=key)   
        return zip_file_path1