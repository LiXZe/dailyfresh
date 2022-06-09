"""dailyfresh URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    # 做url反向解析，动态获取url地址。namespace是放在include里面，include还需要传一个元组，元组内还要有app_name。
    path('user/', include(('user.urls', 'user'), namespace='user')),  # 用户模块
    path('cart/', include(('cart.urls', 'cart'), namespace='cart')),  # 购物车模块
    path('order/', include(('order.urls', 'order'), namespace='order')),  # 订单模块
    path('tinymce/', include('tinymce.urls')),  # 富文本编辑器
    path('search/', include('haystack.urls')),  # 全文检索框架
    path('', include(('goods.urls', 'goods'), namespace='goods')),  # 商品模块,作为主页
]
