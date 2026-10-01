"""
ASGI config for whatsapp project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""
import os

from django.core.asgi import get_asgi_application

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

from chat.routing import websocket_urlpatterns


os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "whatsapp.settings"
)


django_asgi_app = get_asgi_application()


application = ProtocolTypeRouter({

    # Normal HTTP requests
    "http": django_asgi_app,

    # WebSocket requests
    "websocket": AuthMiddlewareStack(

        URLRouter(
            websocket_urlpatterns
        )

    ),

})