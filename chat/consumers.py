import asyncio
import json

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from django.utils import timezone

from .models import Message, UserStatus, Profile


ONLINE = {}    # user_id -> us user ke kitne tab/connection khule hain
IN_CHAT = {}   # (user_id, other_id) -> user_id ne other_id ki chat kitne tab mein khol rakhi hai


def user_card(user):
    """Chat list ke liye user ka naam aur DP ka url."""
    try:
        profile = user.profile
        name = profile.full_name or user.username
        dp = profile.dp.url if profile.dp else ""
    except Profile.DoesNotExist:
        name, dp = user.username, ""
    return name, dp


@database_sync_to_async
def mark_seen(me_id, other_id):
    """other ke bheje saare unseen messages ko 'seen' karo. Unki IDs wapas do."""
    now = timezone.now()
    ids = list(
        Message.objects.filter(
            sender_user_id=other_id,
            receiver_user_id=me_id,
            seen_at__isnull=True,
        ).values_list("id", flat=True)
    )
    if ids:
        Message.objects.filter(id__in=ids, delivered_at__isnull=True).update(delivered_at=now)
        Message.objects.filter(id__in=ids).update(seen_at=now)
    return ids


@database_sync_to_async
def mark_delivered(me_id):
    """Mere liye aaye saare pending messages ko 'delivered' karo. {sender_id: [ids]} do."""
    rows = list(
        Message.objects.filter(
            receiver_user_id=me_id,
            delivered_at__isnull=True,
            sender_user__isnull=False,
        ).values_list("id", "sender_user_id")
    )
    if rows:
        Message.objects.filter(id__in=[r[0] for r in rows]).update(delivered_at=timezone.now())

    out = {}
    for message_id, sender_id in rows:
        out.setdefault(sender_id, []).append(message_id)
    return out


@database_sync_to_async
def save_last_seen(user_id):
    now = timezone.now()
    UserStatus.objects.update_or_create(
        user_id=user_id,
        defaults={"last_seen": now}
    )
    return now.isoformat()


# ---------------------------------------------------------------
#  CHAT: messages + ticks (sent / delivered / seen)
# ---------------------------------------------------------------

class ChatConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope["user"]

        # Login nahi hai toh chat socket nahi khulega
        if not user.is_authenticated:
            await self.close()
            return

        # Receiver ki ID (URL se) aur current logged-in user ki ID
        self.user_id = int(self.scope["url_route"]["kwargs"]["user_id"])
        self.current_user_id = user.id

        # Dono users ke liye ek common chat group
        user_ids = sorted([self.current_user_id, self.user_id])
        self.room_group_name = f"chat_{user_ids[0]}_{user_ids[1]}"

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Yaad rakho ki main is chat ko abhi khol ke baitha hu
        key = (self.current_user_id, self.user_id)
        IN_CHAT[key] = IN_CHAT.get(key, 0) + 1

        # Chat khulte hi saamne wale ke saare messages 'seen' ho jaate hain
        seen_ids = await mark_seen(self.current_user_id, self.user_id)
        if seen_ids:
            # 1) saamne wale ko batao: tumhare messages dekh liye gaye (blue tick)
            await self.channel_layer.group_send(
                f"presence_user_{self.user_id}",
                {"type": "ticks_event", "ids": seen_ids, "status": "seen"},
            )
            # 2) meri apni chat list se unread badge hata do
            await self.channel_layer.group_send(
                f"presence_user_{self.current_user_id}",
                {"type": "read_event", "from_id": self.user_id},
            )

        print("WebSocket connected:", self.current_user_id, "→", self.user_id)

    async def disconnect(self, close_code):
        if not hasattr(self, "room_group_name"):
            return

        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        key = (self.current_user_id, self.user_id)
        IN_CHAT[key] = IN_CHAT.get(key, 1) - 1
        if IN_CHAT[key] <= 0:
            IN_CHAT.pop(key, None)

        print("WebSocket disconnected:", self.current_user_id)

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message", "").strip()

        if not message:
            return

        # Message ka status: saamne wale ki halat dekh ke
        if IN_CHAT.get((self.user_id, self.current_user_id), 0) > 0:
            status = "seen"          # wo isi chat mein baitha hai
        elif ONLINE.get(self.user_id, 0) > 0:
            status = "delivered"     # online hai par chat khuli nahi
        else:
            status = "sent"          # offline hai

        info = await self.save_message(message, status)

        # Chat ke dono participants ko message bhejna
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "id": info["id"],
                "message": message,
                "sender_id": self.current_user_id,
                "created_at": info["created_at"],
                "status": status,
            }
        )

        # Dono ki chat list ko live update (naya preview, unread count)
        event = {
            "type": "new_message_event",
            "id": info["id"],
            "text": message,
            "from_id": self.current_user_id,
            "to_id": self.user_id,
            "created_at": info["created_at"],
            "status": status,
            "from_name": info["from_name"],
            "from_dp": info["from_dp"],
            "to_name": info["to_name"],
            "to_dp": info["to_dp"],
        }
        await self.channel_layer.group_send(f"presence_user_{self.user_id}", event)
        await self.channel_layer.group_send(f"presence_user_{self.current_user_id}", event)

    @database_sync_to_async
    def save_message(self, text, status):
        sender = self.scope["user"]
        receiver = User.objects.get(id=self.user_id)

        now = timezone.now()
        extra = {}
        if status in ("delivered", "seen"):
            extra["delivered_at"] = now
        if status == "seen":
            extra["seen_at"] = now

        msg = Message.objects.create(
            sender=sender.username,
            sender_user=sender,
            receiver_user=receiver,
            text=text,
            **extra
        )

        from_name, from_dp = user_card(sender)
        to_name, to_dp = user_card(receiver)

        return {
            "id": msg.id,
            "created_at": msg.created_at.isoformat(),
            "from_name": from_name,
            "from_dp": from_dp,
            "to_name": to_name,
            "to_dp": to_dp,
        }

    async def chat_message(self, event):
        await self.send(
            text_data=json.dumps({
                "type": "chat_message",
                "id": event["id"],
                "message": event["message"],
                "sender_id": event["sender_id"],
                "created_at": event["created_at"],
                "status": event["status"],
            })
        )


# ---------------------------------------------------------------
#  PRESENCE: online / last seen / typing / ticks / chat list
# ---------------------------------------------------------------

class PresenceConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope["user"]

        if not user.is_authenticated:
            await self.close()
            return

        self.uid = user.id

        # "presence" group: online/offline sabko batane ke liye
        # "presence_user_<id>" group: sirf is user ko typing, ticks, naye messages bhejne ke liye
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

        # Jo messages mere offline rehte aaye the, wo ab 'delivered' ho gaye
        delivered = await mark_delivered(self.uid)
        for sender_id, ids in delivered.items():
            await self.channel_layer.group_send(
                f"presence_user_{sender_id}",
                {"type": "ticks_event", "ids": ids, "status": "delivered"},
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

    async def ticks_event(self, event):
        await self.send(json.dumps({
            "type": "ticks",
            "ids": event["ids"],
            "status": event["status"],
        }))

    async def read_event(self, event):
        await self.send(json.dumps({
            "type": "read",
            "from_id": event["from_id"],
        }))

    async def new_message_event(self, event):
        await self.send(json.dumps({
            "type": "new_message",
            "id": event["id"],
            "text": event["text"],
            "from_id": event["from_id"],
            "to_id": event["to_id"],
            "created_at": event["created_at"],
            "status": event["status"],
            "from_name": event["from_name"],
            "from_dp": event["from_dp"],
            "to_name": event["to_name"],
            "to_dp": event["to_dp"],
        }))