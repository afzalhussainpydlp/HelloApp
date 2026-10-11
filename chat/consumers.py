import asyncio
import json

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Message, UserStatus


class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        # Receiver ki ID (URL se) aur current logged-in user ki ID
        self.user_id = int(
            self.scope["url_route"]["kwargs"]["user_id"]
        )
        self.current_user_id = self.scope["user"].id

        # Dono users ke liye ek common chat group
        user_ids = sorted([
            self.current_user_id,
            self.user_id
        ])
        self.room_group_name = f"chat_{user_ids[0]}_{user_ids[1]}"

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        print("WebSocket connected:", self.current_user_id, "→", self.user_id)

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

        print("WebSocket disconnected:", self.current_user_id)

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message", "").strip()

        if not message:
            return

        # Message database mein save karna
        await self.save_message(message)

        # Chat ke dono participants ko message bhejna
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": message,
                "sender_id": self.current_user_id,
            }
        )

    @database_sync_to_async
    def save_message(self, message):
        sender = self.scope["user"]
        receiver = User.objects.get(id=self.user_id)

        Message.objects.create(
            sender=sender.username,
            sender_user=sender,
            receiver_user=receiver,
            text=message
        )

    async def chat_message(self, event):
        await self.send(
            text_data=json.dumps({
                "type": "chat_message",
                "message": event["message"],
                "sender_id": event["sender_id"],
            })
        )


# ---------------------------------------------------------------
#  PRESENCE: online / last seen / typing
# ---------------------------------------------------------------

ONLINE = {}   # user_id -> us user ke kitne tab/connection khule hain


@database_sync_to_async
def save_last_seen(user_id):
    now = timezone.now()
    UserStatus.objects.update_or_create(
        user_id=user_id,
        defaults={"last_seen": now}
    )
    return now.isoformat()


class PresenceConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope["user"]

        if not user.is_authenticated:
            await self.close()
            return

        self.uid = user.id

        # "presence" group: online/offline sabko batane ke liye
        # "presence_user_<id>" group: sirf is user ko typing bhejne ke liye
        await self.channel_layer.group_add("presence", self.channel_name)
        await self.channel_layer.group_add(f"presence_user_{self.uid}", self.channel_name)

        await self.accept()

        ONLINE[self.uid] = ONLINE.get(self.uid, 0) + 1

        # Abhi jo jura usko batao ki kaun-kaun online hai
        await self.send(json.dumps({
            "type": "snapshot",
            "online": list(ONLINE.keys()),
        }))

        # Pehla tab khula hai toh sabko batao ki ye online aa gaya
        if ONLINE[self.uid] == 1:
            await self.channel_layer.group_send(
                "presence",
                {
                    "type": "presence_update",
                    "user_id": self.uid,
                    "online": True,
                    "last_seen": None,
                }
            )

    async def disconnect(self, close_code):
        if not hasattr(self, "uid"):
            return

        await self.channel_layer.group_discard("presence", self.channel_name)
        await self.channel_layer.group_discard(f"presence_user_{self.uid}", self.channel_name)

        ONLINE[self.uid] = ONLINE.get(self.uid, 1) - 1

        # Abhi bhi koi aur tab khula hai, toh offline mat karo
        if ONLINE[self.uid] > 0:
            return

        # 5 second ruko: agar user sirf doosre page par ja raha hai,
        # toh wo wapas jud jayega aur offline dikhana nahi padega
        await asyncio.sleep(5)

        if ONLINE.get(self.uid, 0) > 0:
            return

        ONLINE.pop(self.uid, None)
        last_seen = await save_last_seen(self.uid)

        await self.channel_layer.group_send(
            "presence",
            {
                "type": "presence_update",
                "user_id": self.uid,
                "online": False,
                "last_seen": last_seen,
            }
        )

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data)
        except (TypeError, ValueError):
            return

        # Browser se "typing" aaya: jisko type kar raha hai sirf usko bhejo
        if data.get("type") == "typing" and isinstance(data.get("to"), int):
            await self.channel_layer.group_send(
                f"presence_user_{data['to']}",
                {
                    "type": "typing_event",
                    "from_id": self.uid,
                }
            )

    async def presence_update(self, event):
        await self.send(json.dumps({
            "type": "update",
            "user_id": event["user_id"],
            "online": event["online"],
            "last_seen": event["last_seen"],
        }))

    async def typing_event(self, event):
        await self.send(json.dumps({
            "type": "typing",
            "from_id": event["from_id"],
        }))