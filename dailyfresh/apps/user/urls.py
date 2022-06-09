from django.urls import path, include, re_path
from apps.user import views
from user.views import RegisterView, ActiveView, LoginView, UserInfoView, UserOrderView, AddressView, LogoutView
from django.contrib.auth.decorators import login_required

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),  # 使用视图类
    re_path(r'active/(?P<token>.*)$', ActiveView.as_view(), name='active'),  # 激活用户
    path('login/', LoginView.as_view(), name='login'),

    path('', UserInfoView.as_view(), name='user'),  # 用户信息-中心
    re_path(r'order/(?P<page>\d+)$', UserOrderView.as_view(), name='order'),  # 用户信息-订单
    path('address/', AddressView.as_view(), name='address'),  # 用户信息-地址
    path('logout/', LogoutView.as_view(), name='logout'),  # 用户注销
    path('testcelery/', views.testCelery)

    # path('', login_required(UserInfoView.as_view()), name='user'),  # 用户信息-中心
    # path('order/', login_required(UserOrderView.as_view()), name='order'),  # 用户信息-订单
    # path('address/', login_required(AddressView.as_view()), name='address'),  # 用户信息-地址
    # path('register', views.register, name='register'),
    # path('register_handle', views.register_handle, name='register_handle')
]
