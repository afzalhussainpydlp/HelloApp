from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("chat/<int:user_id>/", views.chat_page, name="chat_page"),
    path("send-message/", views.send_message, name="send_message"),
    path("new-chat/", views.new_chat, name="new_chat"),

    path("welcome/", views.welcome_view, name="welcome"),
    path("login/", views.phone_view, name="login"),
    path("register/", views.phone_view, name="register_phone"),
    path("verify/", views.otp_view, name="register_otp"),
    path("profile/", views.profile_view, name="register_profile"),
    path("logout/", views.logout_view, name="logout"),
]