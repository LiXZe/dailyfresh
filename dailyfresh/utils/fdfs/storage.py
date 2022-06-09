from django.core.files.storage import Storage
from fdfs_client.client import Fdfs_client
from dailyfresh import settings


class FDFSStorage(Storage):
    """自定义fastdfs文件存储类"""

    def __init__(self, client_conf=None, base_url=None):
        """动态地对storage类进行配置"""

        if client_conf is None:
            client_conf = settings.FDFS_CLIENT_CONF
        self.client_conf = client_conf
        if base_url is None:
            base_url = settings.FDFS_URL
        self.base_url = base_url

    def save(self, name, content, max_length=None):  # 一定需要添加参数max_length
        """name为上传文件的名字，content包含上传文件内容的File对象"""

        client = Fdfs_client(self.client_conf)
        # 上传文件到我的阿里云服务器fastdfs系统中
        result = client.upload_by_buffer(content.read())
        if result.get('Status') != 'Upload successed.':
            # 上传失败
            raise Exception('上传文件到云服务器的fastdfs系统失败！')
        filename = result.get('Remote file_id')

        # save方法最后返回的内容为保存在django系统中的图片文件名称
        return filename

    def url(self, name):
        """返回访问文件的url地址,name为保存在django数据表中的为文件名"""

        return self.base_url + name
