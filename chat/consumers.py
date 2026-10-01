from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

import json

from django.contrib.auth.models import User
from .models import Message


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        # URL se receiver/user ID lena
        self.user_id = self.scope["url_route"]["kwargs"]["user_id"]

        # Current logged-in user ki ID
        self.current_user_id = self.scope["user"].id

        # Dono users ki IDs ko fixed order mein rakhna
        user_ids = sorted([
            self.current_user_id,
            int(self.user_id)
        ])

        # Dono users ke liye same chat room
        self.room_group_name = (
            f"chat_{user_ids[0]}_{user_ids[1]}"
        )

        # Chat group join karna
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        # WebSocket accept karna
        await self.accept()

        print(
            "WebSocket connected:",
            self.current_user_id,
            "→",
            self.user_id,
            "ROOM:",
            self.room_group_name
        )

    async def disconnect(self, close_code):
        # Chat group se remove hona
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

        print(
            "WebSocket disconnected:",
            self.current_user_id
        )

    @database_sync_to_async
    def save_message(self, message):
        # Receiver user ko database se find karna
        receiver = User.objects.get(
            id=self.user_id
        )

        # Message ko database mein save karna
        Message.objects.create(
            sender=self.scope["user"].username,
            sender_user=self.scope["user"],
            receiver_user=receiver,
            text=message
        )

    async def receive(self, text_data):
        # JSON ko Python dictionary mein convert karna
        data = json.loads(text_data)

        # Message lena
        message = data["message"]

        print(
            "WebSocket message:",
            self.current_user_id,
            "→",
            self.user_id,
            ":",
            message
        )

        # Message ko database mein save karna
        await self.save_message(message)

        # Same chat room ke sabhi connected users ko message bhejna
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": message,
                "sender_id": self.current_user_id,
            }
        )

    async def chat_message(self, event):
         # event se msg lena 
         message = event["message"]
         
         #sender ki id lena
         
         sender_id = event["sender_id"]
         
         #browser ko msgt bhejna
         
         await self.send(
             text_data = json.dumps({
                 "message": message,
                 "sender_id": sender_id
             })
        )