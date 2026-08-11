from django.urls import path
from . import views


urlpatterns = [
    path("",views.home, name = "home"),
    path("chat/<int:user_id>/", views.chat_page, name="chat_page"),
    path("send-message/", views.send_message, name="send_message"),
    path("login/", views.login_view, name="login"),
]