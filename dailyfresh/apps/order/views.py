from django.shortcuts import render, redirect
from django.urls import reverse
from django.http import JsonResponse
from django.db import transaction
from django.conf import settings
from django.views.generic import View
from django.core.exceptions import ObjectDoesNotExist

from user.models import Address
from goods.models import GoodsSKU
from order.models import OrderInfo, OrderGoods

from django_redis import get_redis_connection
from utils.mixin import LoginRequiredMixin
from datetime import datetime
# from alipay import AliPay
import os

from alipay.aop.api.AlipayClientConfig import AlipayClientConfig
from alipay.aop.api.DefaultAlipayClient import DefaultAlipayClient
from alipay.aop.api.domain.AlipayTradePagePayModel import AlipayTradePagePayModel
from alipay.aop.api.domain.SettleDetailInfo import SettleDetailInfo
from alipay.aop.api.domain.SettleInfo import SettleInfo
from alipay.aop.api.domain.SubMerchant import SubMerchant
from alipay.aop.api.request.AlipayTradePagePayRequest import AlipayTradePagePayRequest
import logging

from alipay.aop.api.domain.AlipayTradeQueryModel import AlipayTradeQueryModel
from alipay.aop.api.request.AlipayTradeQueryRequest import AlipayTradeQueryRequest
import traceback
from alipay.aop.api.response.AlipayTradeQueryResponse import AlipayTradeQueryResponse


# Create your views here.


# /order/place
class OrderPlaceView(LoginRequiredMixin, View):
    """提交订单页面显示"""

    def post(self, request):
        """提交订单页面显示"""

        # 获取登录的用户
        user = request.user
        # 获取参数sku_ids
        sku_ids = request.POST.getlist('sku_ids')  # [1,26]

        # 校验参数
        if not sku_ids:
            # 跳转到购物车页面
            return redirect(reverse('cart:show'))

        conn = get_redis_connection('default')
        cart_key = 'cart_%d' % user.id

        skus = []
        # 保存商品的总件数和总价格
        total_count = 0
        total_price = 0
        # 遍历sku_ids获取用户要购买的商品的信息
        for sku_id in sku_ids:
            # 根据商品的id获取商品的信息
            sku = GoodsSKU.objects.get(id=sku_id)
            # 获取用户所要购买的商品的数量
            count = conn.hget(cart_key, sku_id)
            # 计算商品的小计
            amount = sku.price * int(count)
            # 动态给sku增加属性count,保存购买商品的数量
            sku.count = count
            # 动态给sku增加属性amount,保存购买商品的小计
            sku.amount = amount
            # 追加
            skus.append(sku)
            # 累加计算商品的总件数和总价格
            total_count += int(count)
            total_price += amount

        # 运费:实际开发的时候，属于一个子系统
        transit_price = 10  # 写死

        # 实付款
        total_pay = total_price + transit_price

        # 获取用户的收件地址
        addrs = Address.objects.filter(user=user)

        # 组织上下文
        sku_ids = ','.join(sku_ids)  # [1,25]->1,25
        context = {'skus': skus,
                   'total_count': total_count,
                   'total_price': total_price,
                   'transit_price': transit_price,
                   'total_pay': total_pay,
                   'addrs': addrs,
                   'sku_ids': sku_ids}

        # 使用模板
        return render(request, 'place_order.html', context)


# 前端传递的参数:地址id(addr_id) 支付方式(pay_method) 用户要购买的商品id字符串(sku_ids)
# mysql事务: 一组sql操作，要么都成功，要么都失败
# 高并发:秒杀
# 支付宝支付
class OrderCommitView1(View):
    """订单创建（悲观锁版本）"""

    @transaction.atomic
    def post(self, request):
        """订单创建"""

        # 判断用户是否登录
        user = request.user
        if not user.is_authenticated:
            # 用户未登录
            return JsonResponse({'res': 0, 'errmsg': '用户未登录'})

        # 接收参数
        addr_id = request.POST.get('addr_id')
        pay_method = request.POST.get('pay_method')
        sku_ids = request.POST.get('sku_ids')  # 1,3

        # 校验参数
        if not all([addr_id, pay_method, sku_ids]):
            return JsonResponse({'res': 1, 'errmsg': '参数不完整'})

        # 校验支付方式
        if pay_method not in OrderInfo.PAY_METHODS.keys():
            return JsonResponse({'res': 2, 'errmsg': '非法的支付方式'})

        # 校验地址
        try:
            addr = Address.objects.get(id=addr_id)
        except ObjectDoesNotExist:
            # 地址不存在
            return JsonResponse({'res': 3, 'errmsg': '地址非法'})

        # todo: 创建订单核心业务

        # 组织参数
        # 订单id: 20171122181630+用户id
        order_id = datetime.now().strftime('%Y%m%d%H%M%S') + str(user.id)

        # 运费
        transit_price = 10

        # 总数目和总金额
        total_count = 0
        total_price = 0

        # 设置事务保存点
        save_id = transaction.savepoint()
        try:
            # todo: 向df_order_info表中添加一条记录
            order = OrderInfo.objects.create(order_id=order_id,
                                             user=user,
                                             addr=addr,
                                             pay_method=pay_method,
                                             total_count=total_count,
                                             total_price=total_price,
                                             transit_price=transit_price)

            # todo: 用户的订单中有几个商品，需要向df_order_goods表中加入几条记录
            conn = get_redis_connection('default')
            cart_key = 'cart_%d' % user.id

            sku_ids = sku_ids.split(',')
            for sku_id in sku_ids:
                # 获取商品的信息
                try:
                    # select * from df_goods_sku where id=sku_id for update;(加锁)
                    sku = GoodsSKU.objects.select_for_update().get(id=sku_id)
                except:
                    # 商品不存在
                    transaction.savepoint_rollback(save_id)
                    return JsonResponse({'res': 4, 'errmsg': '商品不存在'})

                print('user:%d stock:%d' % (user.id, sku.stock))
                # import time
                # time.sleep(10)

                # 从redis中获取用户所要购买的商品的数量
                count = conn.hget(cart_key, sku_id)

                # todo: 判断商品的库存
                if int(count) > sku.stock:
                    transaction.savepoint_rollback(save_id)
                    return JsonResponse({'res': 6, 'errmsg': '商品库存不足'})

                # todo: 向df_order_goods表中添加一条记录
                OrderGoods.objects.create(order=order,
                                          sku=sku,
                                          count=count,
                                          price=sku.price)

                # todo: 更新商品的库存和销量
                sku.stock -= int(count)
                sku.sales += int(count)
                sku.save()

                # todo: 累加计算订单商品的总数量和总价格
                amount = sku.price * int(count)
                total_count += int(count)
                total_price += amount

            # todo: 更新订单信息表中的商品的总数量和总价格
            order.total_count = total_count
            order.total_price = total_price
            order.save()
        except Exception as e:
            transaction.savepoint_rollback(save_id)
            return JsonResponse({'res': 7, 'errmsg': '下单失败'})

        # 提交事务
        transaction.savepoint_commit(save_id)

        # todo: 清除用户购物车中对应的记录
        conn.hdel(cart_key, *sku_ids)

        # 返回应答
        return JsonResponse({'res': 5, 'message': '创建成功'})


class OrderCommitView(View):
    """订单创建（乐观锁版本）"""

    @transaction.atomic
    def post(self, request):
        """订单创建"""

        # 判断用户是否登录
        user = request.user
        if not user.is_authenticated:
            # 用户未登录
            return JsonResponse({'res': 0, 'errmsg': '用户未登录'})

        # 接收参数
        addr_id = request.POST.get('addr_id')
        pay_method = request.POST.get('pay_method')
        sku_ids = request.POST.get('sku_ids')  # 1,3

        # 校验参数
        if not all([addr_id, pay_method, sku_ids]):
            return JsonResponse({'res': 1, 'errmsg': '参数不完整'})

        # 校验支付方式
        if pay_method not in OrderInfo.PAY_METHODS.keys():
            return JsonResponse({'res': 2, 'errmsg': '非法的支付方式'})

        # 校验地址
        try:
            addr = Address.objects.get(id=addr_id)
        except ObjectDoesNotExist:
            # 地址不存在
            return JsonResponse({'res': 3, 'errmsg': '地址非法'})

        # todo: 创建订单核心业务

        # 组织参数
        # 订单id: 20171122181630+用户id
        order_id = datetime.now().strftime('%Y%m%d%H%M%S') + str(user.id)

        # 运费
        transit_price = 10

        # 总数目和总金额
        total_count = 0
        total_price = 0

        # 设置事务保存点
        save_id = transaction.savepoint()
        try:
            # todo: 向df_order_info表中添加一条记录
            order = OrderInfo.objects.create(order_id=order_id,
                                             user=user,
                                             addr=addr,
                                             pay_method=pay_method,
                                             total_count=total_count,
                                             total_price=total_price,
                                             transit_price=transit_price)

            # todo: 用户的订单中有几个商品，需要向df_order_goods表中加入几条记录
            conn = get_redis_connection('default')
            cart_key = 'cart_%d' % user.id

            sku_ids = sku_ids.split(',')
            for sku_id in sku_ids:
                for i in range(3):
                    # 获取商品的信息
                    try:
                        sku = GoodsSKU.objects.get(id=sku_id)
                    except:
                        # 商品不存在
                        transaction.savepoint_rollback(save_id)
                        return JsonResponse({'res': 4, 'errmsg': '商品不存在'})

                    # 从redis中获取用户所要购买的商品的数量
                    count = conn.hget(cart_key, sku_id)

                    # todo: 判断商品的库存
                    if int(count) > sku.stock:
                        transaction.savepoint_rollback(save_id)
                        return JsonResponse({'res': 6, 'errmsg': '商品库存不足'})

                    # todo: 更新商品的库存和销量
                    orgin_stock = sku.stock
                    new_stock = orgin_stock - int(count)
                    new_sales = sku.sales + int(count)

                    # print('user:%d times:%d stock:%d' % (user.id, i, sku.stock))
                    # import time
                    # time.sleep(10)

                    # update df_goods_sku set stock=new_stock, sales=new_sales
                    # where id=sku_id and stock = orgin_stock

                    # 返回受影响的行数
                    res = GoodsSKU.objects.filter(id=sku_id, stock=orgin_stock).update(stock=new_stock, sales=new_sales)
                    if res == 0:
                        if i == 2:
                            # 尝试的第3次
                            transaction.savepoint_rollback(save_id)
                            return JsonResponse({'res': 7, 'errmsg': '下单失败2'})
                        continue

                    # todo: 向df_order_goods表中添加一条记录
                    OrderGoods.objects.create(order=order,
                                              sku=sku,
                                              count=count,
                                              price=sku.price)

                    # todo: 累加计算订单商品的总数量和总价格
                    amount = sku.price * int(count)
                    total_count += int(count)
                    total_price += amount

                    # 跳出循环
                    break

            # todo: 更新订单信息表中的商品的总数量和总价格
            order.total_count = total_count
            order.total_price = total_price
            order.save()
        except Exception as e:
            transaction.savepoint_rollback(save_id)
            return JsonResponse({'res': 7, 'errmsg': '下单失败'})

        # 提交事务
        transaction.savepoint_commit(save_id)

        # todo: 清除用户购物车中对应的记录
        conn.hdel(cart_key, *sku_ids)

        # 返回应答
        return JsonResponse({'res': 5, 'message': '创建成功'})


class NewOrderPayView(View):
    """使用支付宝官方提供的AliPay SDK实现订单支付"""

    def post(self, request):
        """订单支付"""

        # 用户是否登录
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '用户未登录'})

        # 接收参数
        order_id = request.POST.get('order_id')

        # 校验参数
        if not order_id:
            return JsonResponse({'res': 1, 'errmsg': '无效的订单id'})

        try:
            order = OrderInfo.objects.get(order_id=order_id,
                                          user=user,
                                          pay_method=3,
                                          order_status=1)
        except ObjectDoesNotExist:
            return JsonResponse({'res': 2, 'errmsg': '订单错误'})

        # 业务处理:使用python sdk调用支付宝的支付接口
        # 初始化
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s %(levelname)s %(message)s',
            filemode='a', )
        logger = logging.getLogger('')
        """
        设置配置，包括支付宝网关地址、app_id、应用私钥、支付宝公钥等，其他配置值可以查看AlipayClientConfig的定义。
        """
        alipay_client_config = AlipayClientConfig()
        alipay_client_config.server_url = 'https://openapi.alipaydev.com/gateway.do'
        alipay_client_config.app_id = '2021000120611494'
        alipay_client_config.app_private_key = 'MIIEogIBAAKCAQEAjadhTQnlo5AB3HEyuduHEnPllMsXXwOyBKqW9Kh0xGNDBgWE7ftN00J741bQEaTRP6615yoS9qoKWC8F4Xeo2SMlwxnUC4kdjmbsg/MPND5Tcd7GER02WHgO3A1p6mLuvVSrdmFf+pmpahAZp4dGquSyfh9TuMSF7PmxzJOobhg7gpz9roWaEiwuHjYmHPcbA/ezOcDAngnB/uW3s+jeDDzS9VpZ/J3hYq1G7FxJHCSLS2JK1ucmQdZ0I6eKW97X7ZKy5wbVjk7C19GIa0QMGaqxXsOvzP/jlS1929ZstKXNuGhWxe63PSHTeXOUfAoGzPhzNngBByTFuEqf9XtjXwIDAQABAoIBAGygi9Q5H7nDSDoVMJIoT7eN2uO0rnJ1cgF0FBkASbMEb7RhbSPWkELOfBTFUQAGxVQxlVr0/9/aW95uCPNzAK+q7V1lJB/IgTrfoMY7EVC97t2muXsMTM2hG7wSKUPNpEjacjZwy9pwFeO4/wNikIWipWJkgZ5yTkWA4aEBJqttkf4SUiNZVkLLPRwzhkxeqjzsiKd0JngM3zB0HOsZq7xlF06xohfkY/0QhmbIbCHpryUggQw1bdhq7dPYKqwJSuytyo2jGuwDnguzGfLt5NTu92e/3QYKhL+cV/emmtkNPop7EVyoEAGWM4+7I3IvbbKiC1guj8DBGLWBNDL+HOECgYEA1GeZRbOMP7PZ/4wVAzRVFizf9VXJIa640oreEyg14sSHHChy9Tnrp0eL164O2Rk8spw/W/UoP/Sb9JAbwDk6Vy6nn9rhC6Dy4S/yGrdQx1WsP45EhAlK8YniBCusr/ktDtJFUdHswomHmhS9B72Cuptmngwbs25xUiCQmOu7qBECgYEAqrpP2D8M7Ols8LL2ASRwiNK343EzD5048oXtkMu+7WoWwLgbKxIDwQ8sPHTZFcHjm6qPFD8WZVZRKNRtEO7qQLTZZxvJkUSeTKjzsDz8Jfq8+3IcruKKwnJjXMaRXQLTTPJSvTFe5j30XmUu6SOtVXGVwUnKNna/EXO+j//pRG8CgYALj4PxHj+g6/oOhaJTJVMIPPMHmF61HZxjoTlLE6IzMH0mFDGFlVx1I1jCKXAXct8x3X05VROWv4qJehp4kOTS//ARrEkZZ+4wQXrM53NXFhuk7epewV653MZXccUZYteH+fvZ4zIeuHuP8FcFh9QIshKYwZH0Zyt1y93y5Mm28QKBgFSiF4KzfszSylZn4NugHMk27EGrlAxhfCF9ks78saEvvie7HHy1aXcC9oLhydunShWY72SyAYAq0gDTjV2fkjCRdlROyhVhVrfZ6TOsh7KsIZLkdqObWf4ahncvWFArXmP+nFV9a/XnMIS61A5uyTZaIEq5MazeIMmd/xGTYyexAoGADEZwCIv2M62+hJ8SS+/Ljqn1SBMoRfBKNA9zKY46u9EbVVjvdzy/4uT6mSO5arZRaog2KX9GowgLGzDhn4TjFcYZWFNs/mnq6SISJlcDLnePaqtu6kvx92vKxgMyQMAiMiAXMXv5n6PaUrs+L3mao5wvwoJrGjxCgfvItV1UlcI='
        alipay_client_config.alipay_public_key = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtWxJ5W9FsPUteeGe0u7OGKv2+SSwbI/XMVEmBm6aqtyu/dXnZ7o9awLJ/LL2CAxZeEYRxSjlx9OKG2r+6RNQ20mATU5KP/CAp44QJZuoPjoswWgMJgb58DaZSzc4KJyZ2jjTYS7tUyMlKnL2UHtPdgq6GgL8GH2RjdALZ9NRtHEZYNzgigIy9JW4qD0hLNYN23AJ69Ku8HhMlSJmjD0MVM4BFmR7F8c28bex9gvTPHwoJht0XQKyJaaL78JFVRefH/kkd047eJVueuhAMetU3EWrCfqKEGWpt0FOEwlwfbZDrlNF6rkkSr9b4g9OgpDlebeZd8/ZLfMu7jQ+X/dj5QIDAQAB'
        alipay_client_config.charset = 'utf8'

        """
        得到客户端对象。
        注意，一个alipay_client_config对象对应一个DefaultAlipayClient，定义DefaultAlipayClient对象后，alipay_client_config不得修改，如果想使用不同的配置，请定义不同的DefaultAlipayClient。
        logger参数用于打印日志，不传则不打印，建议传递。
        """
        client = DefaultAlipayClient(alipay_client_config=alipay_client_config, logger=logger)

        """
        页面接口示例：alipay.trade.page.pay
        """
        # 对照接口文档，构造请求对象
        model = AlipayTradePagePayModel()
        total_pay = order.total_price + order.transit_price  # Decimal
        model.out_trade_no = order_id
        model.total_amount = str(total_pay)
        model.subject = '天天生鲜%s' % order_id
        model.body = "支付宝测试"
        model.product_code = "FAST_INSTANT_TRADE_PAY"
        settle_detail_info = SettleDetailInfo()
        settle_detail_info.amount = str(total_pay)
        settle_detail_info.trans_in_type = "userId"
        settle_detail_info.trans_in = "2088621959311742"  # 商户ID(PID)
        settle_detail_infos = list()
        settle_detail_infos.append(settle_detail_info)
        settle_info = SettleInfo()
        settle_info.settle_detail_infos = settle_detail_infos
        model.settle_info = settle_info

        """这个是设置子商户ID用的"""
        # sub_merchant = SubMerchant()
        # sub_merchant.merchant_id = "2088622959335343"  # 子商户的商户id
        # model.sub_merchant = sub_merchant

        request = AlipayTradePagePayRequest(biz_model=model)
        # 得到构造的请求，如果http_method是GET，则是一个带完成请求参数的url，如果http_method是POST，则是一段HTML表单片段
        response = client.page_execute(request, http_method="GET")
        # print("alipay.trade.page.pay response:" + response)

        return JsonResponse({'res': 3, 'pay_url': response})


class NewCheckPayView(View):
    """查看订单支付的结果"""

    def post(self, request):
        """查询支付结果"""

        # 用户是否登录
        user = request.user
        if not user.is_authenticated:
            return JsonResponse({'res': 0, 'errmsg': '用户未登录'})

        # 接收参数
        order_id = request.POST.get('order_id')

        # 校验参数
        if not order_id:
            return JsonResponse({'res': 1, 'errmsg': '无效的订单id'})

        try:
            order = OrderInfo.objects.get(order_id=order_id,
                                          user=user,
                                          pay_method=3,
                                          order_status=1)
        except ObjectDoesNotExist:
            return JsonResponse({'res': 2, 'errmsg': '订单错误'})

        # 业务处理:使用python sdk调用支付宝的支付接口
        # 初始化
        # logging.basicConfig(
        #     level=logging.INFO,
        #     format='%(asctime)s %(levelname)s %(message)s',
        #     filemode='a', )
        # logger = logging.getLogger('')
        """
        设置配置，包括支付宝网关地址、app_id、应用私钥、支付宝公钥等，其他配置值可以查看AlipayClientConfig的定义。
        """
        alipay_client_config = AlipayClientConfig()
        alipay_client_config.server_url = 'https://openapi.alipaydev.com/gateway.do'
        alipay_client_config.app_id = '2021000120611494'
        alipay_client_config.app_private_key = 'MIIEogIBAAKCAQEAjadhTQnlo5AB3HEyuduHEnPllMsXXwOyBKqW9Kh0xGNDBgWE7ftN00J741bQEaTRP6615yoS9qoKWC8F4Xeo2SMlwxnUC4kdjmbsg/MPND5Tcd7GER02WHgO3A1p6mLuvVSrdmFf+pmpahAZp4dGquSyfh9TuMSF7PmxzJOobhg7gpz9roWaEiwuHjYmHPcbA/ezOcDAngnB/uW3s+jeDDzS9VpZ/J3hYq1G7FxJHCSLS2JK1ucmQdZ0I6eKW97X7ZKy5wbVjk7C19GIa0QMGaqxXsOvzP/jlS1929ZstKXNuGhWxe63PSHTeXOUfAoGzPhzNngBByTFuEqf9XtjXwIDAQABAoIBAGygi9Q5H7nDSDoVMJIoT7eN2uO0rnJ1cgF0FBkASbMEb7RhbSPWkELOfBTFUQAGxVQxlVr0/9/aW95uCPNzAK+q7V1lJB/IgTrfoMY7EVC97t2muXsMTM2hG7wSKUPNpEjacjZwy9pwFeO4/wNikIWipWJkgZ5yTkWA4aEBJqttkf4SUiNZVkLLPRwzhkxeqjzsiKd0JngM3zB0HOsZq7xlF06xohfkY/0QhmbIbCHpryUggQw1bdhq7dPYKqwJSuytyo2jGuwDnguzGfLt5NTu92e/3QYKhL+cV/emmtkNPop7EVyoEAGWM4+7I3IvbbKiC1guj8DBGLWBNDL+HOECgYEA1GeZRbOMP7PZ/4wVAzRVFizf9VXJIa640oreEyg14sSHHChy9Tnrp0eL164O2Rk8spw/W/UoP/Sb9JAbwDk6Vy6nn9rhC6Dy4S/yGrdQx1WsP45EhAlK8YniBCusr/ktDtJFUdHswomHmhS9B72Cuptmngwbs25xUiCQmOu7qBECgYEAqrpP2D8M7Ols8LL2ASRwiNK343EzD5048oXtkMu+7WoWwLgbKxIDwQ8sPHTZFcHjm6qPFD8WZVZRKNRtEO7qQLTZZxvJkUSeTKjzsDz8Jfq8+3IcruKKwnJjXMaRXQLTTPJSvTFe5j30XmUu6SOtVXGVwUnKNna/EXO+j//pRG8CgYALj4PxHj+g6/oOhaJTJVMIPPMHmF61HZxjoTlLE6IzMH0mFDGFlVx1I1jCKXAXct8x3X05VROWv4qJehp4kOTS//ARrEkZZ+4wQXrM53NXFhuk7epewV653MZXccUZYteH+fvZ4zIeuHuP8FcFh9QIshKYwZH0Zyt1y93y5Mm28QKBgFSiF4KzfszSylZn4NugHMk27EGrlAxhfCF9ks78saEvvie7HHy1aXcC9oLhydunShWY72SyAYAq0gDTjV2fkjCRdlROyhVhVrfZ6TOsh7KsIZLkdqObWf4ahncvWFArXmP+nFV9a/XnMIS61A5uyTZaIEq5MazeIMmd/xGTYyexAoGADEZwCIv2M62+hJ8SS+/Ljqn1SBMoRfBKNA9zKY46u9EbVVjvdzy/4uT6mSO5arZRaog2KX9GowgLGzDhn4TjFcYZWFNs/mnq6SISJlcDLnePaqtu6kvx92vKxgMyQMAiMiAXMXv5n6PaUrs+L3mao5wvwoJrGjxCgfvItV1UlcI='
        alipay_client_config.alipay_public_key = 'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtWxJ5W9FsPUteeGe0u7OGKv2+SSwbI/XMVEmBm6aqtyu/dXnZ7o9awLJ/LL2CAxZeEYRxSjlx9OKG2r+6RNQ20mATU5KP/CAp44QJZuoPjoswWgMJgb58DaZSzc4KJyZ2jjTYS7tUyMlKnL2UHtPdgq6GgL8GH2RjdALZ9NRtHEZYNzgigIy9JW4qD0hLNYN23AJ69Ku8HhMlSJmjD0MVM4BFmR7F8c28bex9gvTPHwoJht0XQKyJaaL78JFVRefH/kkd047eJVueuhAMetU3EWrCfqKEGWpt0FOEwlwfbZDrlNF6rkkSr9b4g9OgpDlebeZd8/ZLfMu7jQ+X/dj5QIDAQAB'

        """
        得到客户端对象。
        注意，一个alipay_client_config对象对应一个DefaultAlipayClient，定义DefaultAlipayClient对象后，alipay_client_config不得修改，如果想使用不同的配置，请定义不同的DefaultAlipayClient。
        logger参数用于打印日志，不传则不打印，建议传递。
        """
        client = DefaultAlipayClient(alipay_client_config=alipay_client_config)

        """
        页面接口示例：alipay.trade.query
        """
        # 对照接口文档，构造请求对象
        while True:
            # 构造请求参数对象
            model = AlipayTradeQueryModel()
            model.out_trade_no = order_id

            request = AlipayTradeQueryRequest(biz_model=model)
            # 执行API调用
            response_content = False
            try:
                response_content = client.execute(request)
            except Exception as e:
                print(traceback.format_exc())
            if not response_content:
                print("failed execute")
            else:
                # 解析响应结果
                response = AlipayTradeQueryResponse()
                response.parse_response_content(response_content)
                # 响应成功的业务处理
                if response.is_success():
                    # 如果业务成功，可以通过response属性获取需要的值
                    # print("get response trade_no:" + response.trade_no)
                    # print("get response response_body:" + response.body)
                    code = response.code

                    if code == '10000' and response.trade_status == 'TRADE_SUCCESS':
                        # 支付成功
                        # 获取支付宝交易号
                        trade_no = response.trade_no
                        # 更新订单状态
                        order.trade_no = trade_no
                        order.order_status = 4  # 待评价
                        order.save()
                        # 返回结果
                        return JsonResponse({'res': 3, 'message': '支付成功'})
                    elif code == '40004' or (code == '10000' and response.trade_status == 'WAIT_BUYER_PAY'):
                        # 等待买家付款
                        # 40004表示业务处理失败，可能一会就会成功
                        import time
                        time.sleep(5)
                        continue
                    else:
                        # 支付出错
                        print(response.code + "," + response.msg + "," + response.sub_code + "," + response.sub_msg)
                        return JsonResponse({'res': 4, 'errmsg': '支付失败'})
                # 响应失败的业务处理
                else:
                    # 如果业务失败，可以从错误码中可以得知错误情况，具体错误码信息可以查看接口文档
                    print(response.code + "," + response.msg + "," + response.sub_code + "," + response.sub_msg)


class CommentView(LoginRequiredMixin, View):
    """订单评论"""

    def get(self, request, order_id):
        """提供评论页面"""
        user = request.user

        # 校验数据
        if not order_id:
            return redirect(reverse('user:order'))

        try:
            order = OrderInfo.objects.get(order_id=order_id, user=user)
        except OrderInfo.DoesNotExist:
            return redirect(reverse("user:order"))

        # 根据订单的状态获取订单的状态标题
        order.status_name = OrderInfo.ORDER_STATUS[order.order_status]

        # 获取订单商品信息
        order_skus = OrderGoods.objects.filter(order_id=order_id)
        for order_sku in order_skus:
            # 计算商品的小计
            amount = order_sku.count * order_sku.price
            # 动态给order_sku增加属性amount,保存商品小计
            order_sku.amount = amount
        # 动态给order增加属性order_skus, 保存订单商品信息
        order.order_skus = order_skus

        # 使用模板
        return render(request, "order_comment.html", {"order": order})

    def post(self, request, order_id):
        """处理评论内容"""
        user = request.user
        # 校验数据
        if not order_id:
            return redirect(reverse('user:order'))

        try:
            order = OrderInfo.objects.get(order_id=order_id, user=user)
        except OrderInfo.DoesNotExist:
            return redirect(reverse("user:order"))

        # 获取评论条数
        total_count = request.POST.get("total_count")
        total_count = int(total_count)

        # 循环获取订单中商品的评论内容
        for i in range(1, total_count + 1):
            # 获取评论的商品的id
            sku_id = request.POST.get("sku_%d" % i)  # sku_1 sku_2
            # 获取评论的商品的内容
            content = request.POST.get('content_%d' % i, '')  # cotent_1 content_2 content_3
            try:
                order_goods = OrderGoods.objects.get(order=order, sku_id=sku_id)
            except OrderGoods.DoesNotExist:
                continue

            order_goods.comment = content
            order_goods.save()

        order.order_status = 5  # 已完成
        order.save()

        return redirect(reverse("user:order", kwargs={"page": 1}))


# ajax post
# 前端传递的参数:订单id(order_id)
# /order/pay/
# class OrderPayView(View):
#     """订单支付，使用GitHub上的支付宝SDK"""
#
#     def post(self, request):
#         """订单支付"""
#
#         # 用户是否登录
#         user = request.user
#         if not user.is_authenticated:
#             return JsonResponse({'res': 0, 'errmsg': '用户未登录'})
#
#         # 接收参数
#         order_id = request.POST.get('order_id')
#
#         # 校验参数
#         if not order_id:
#             return JsonResponse({'res': 1, 'errmsg': '无效的订单id'})
#
#         try:
#             order = OrderInfo.objects.get(order_id=order_id,
#                                           user=user,
#                                           pay_method=3,
#                                           order_status=1)
#         except ObjectDoesNotExist:
#             return JsonResponse({'res': 2, 'errmsg': '订单错误'})
#
#         # 业务处理:使用python sdk调用支付宝的支付接口
#         # 初始化
#         alipay = AliPay(
#             appid="2016090800464054",  # 应用id
#             app_notify_url=None,  # 默认回调url
#             app_private_key_path=os.path.join(settings.BASE_DIR, 'apps/order/app_private_key.pem'),
#             alipay_public_key_path=os.path.join(settings.BASE_DIR, 'apps/order/alipay_public_key.pem'),
#             # 支付宝的公钥，验证支付宝回传消息使用，不是你自己的公钥,
#             sign_type="RSA2",  # RSA 或者 RSA2
#             debug=True  # 默认False
#         )
#
#         # 调用支付接口
#         # 电脑网站支付，需要跳转到https://openapi.alipaydev.com/gateway.do? + order_string
#         total_pay = order.total_price + order.transit_price  # Decimal
#         order_string = alipay.api_alipay_trade_page_pay(
#             out_trade_no=order_id,  # 订单id
#             total_amount=str(total_pay),  # 支付总金额
#             subject='天天生鲜%s' % order_id,
#             return_url=None,
#             notify_url=None  # 可选, 不填则使用默认notify url
#         )
#
#         # 返回应答
#         pay_url = 'https://openapi.alipaydev.com/gateway.do?' + order_string
#         return JsonResponse({'res': 3, 'pay_url': pay_url})


# ajax post
# 前端传递的参数:订单id(order_id)
# /order/check
# class CheckPayView(View):
#     """查看订单支付的结果"""
#
#     def post(self, request):
#         """查询支付结果"""
#
#         # 用户是否登录
#         user = request.user
#         if not user.is_authenticated():
#             return JsonResponse({'res': 0, 'errmsg': '用户未登录'})
#
#         # 接收参数
#         order_id = request.POST.get('order_id')
#
#         # 校验参数
#         if not order_id:
#             return JsonResponse({'res': 1, 'errmsg': '无效的订单id'})
#
#         try:
#             order = OrderInfo.objects.get(order_id=order_id,
#                                           user=user,
#                                           pay_method=3,
#                                           order_status=1)
#         except OrderInfo.DoesNotExist:
#             return JsonResponse({'res': 2, 'errmsg': '订单错误'})
#
#         # 业务处理:使用python sdk调用支付宝的支付接口
#         # 初始化
#         alipay = AliPay(
#             appid="2016090800464054",  # 应用id
#             app_notify_url=None,  # 默认回调url
#             app_private_key_path=os.path.join(settings.BASE_DIR, 'apps/order/app_private_key.pem'),
#             alipay_public_key_path=os.path.join(settings.BASE_DIR, 'apps/order/alipay_public_key.pem'),
#             # 支付宝的公钥，验证支付宝回传消息使用，不是你自己的公钥,
#             sign_type="RSA2",  # RSA 或者 RSA2
#             debug=True  # 默认False
#         )
#
#         # 调用支付宝的交易查询接口
#         while True:
#             response = alipay.api_alipay_trade_query(order_id)
#
#             # response = {
#             #         "trade_no": "2017032121001004070200176844", # 支付宝交易号
#             #         "code": "10000", # 接口调用是否成功
#             #         "invoice_amount": "20.00",
#             #         "open_id": "20880072506750308812798160715407",
#             #         "fund_bill_list": [
#             #             {
#             #                 "amount": "20.00",
#             #                 "fund_channel": "ALIPAYACCOUNT"
#             #             }
#             #         ],
#             #         "buyer_logon_id": "csq***@sandbox.com",
#             #         "send_pay_date": "2017-03-21 13:29:17",
#             #         "receipt_amount": "20.00",
#             #         "out_trade_no": "out_trade_no15",
#             #         "buyer_pay_amount": "20.00",
#             #         "buyer_user_id": "2088102169481075",
#             #         "msg": "Success",
#             #         "point_amount": "0.00",
#             #         "trade_status": "TRADE_SUCCESS", # 支付结果
#             #         "total_amount": "20.00"
#             # }
#
#             code = response.get('code')
#
#             if code == '10000' and response.get('trade_status') == 'TRADE_SUCCESS':
#                 # 支付成功
#                 # 获取支付宝交易号
#                 trade_no = response.get('trade_no')
#                 # 更新订单状态
#                 order.trade_no = trade_no
#                 order.order_status = 4  # 待评价
#                 order.save()
#                 # 返回结果
#                 return JsonResponse({'res': 3, 'message': '支付成功'})
#             elif code == '40004' or (code == '10000' and response.get('trade_status') == 'WAIT_BUYER_PAY'):
#                 # 等待买家付款
#                 # 业务处理失败，可能一会就会成功
#                 import time
#                 time.sleep(5)
#                 continue
#             else:
#                 # 支付出错
#                 print(code)
#                 return JsonResponse({'res': 4, 'errmsg': '支付失败'})
#
#
