from django.urls import path, re_path
from order.views import OrderPlaceView, OrderCommitView1, NewOrderPayView, NewCheckPayView, CommentView


urlpatterns = [
    path('place/', OrderPlaceView.as_view(), name='place'),  # 订单页面显示
    path('commit/', OrderCommitView1.as_view(), name='commit'),  # 订单创建
    path('pay/', NewOrderPayView.as_view(), name='pay'),  # 订单支付
    path('check/', NewCheckPayView.as_view(), name='check'),  # 查询支付交易结果
    re_path(r'comment/(?P<order_id>.+)$', CommentView.as_view(), name='comment'),  # 订单评论
]
