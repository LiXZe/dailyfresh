from fdfs_client.client import Fdfs_client


client = Fdfs_client(r'D:\django4.0\dailyfresh\utils\fdfs\client.conf')
# 参数为路径方法不能是client.upload_by_file
ret = client.upload_by_filename(r'D:\django4.0\dailyfresh\static\images\adv01.jpg')
print(ret)
