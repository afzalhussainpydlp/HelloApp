from django.db import models
from django.contrib.auth.models import User


class Message(models.Model):

    sender = models.CharField(max_length=100)

    sender_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True
    )

    receiver_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="received_messages",
        null=True,
        blank=True
    )

    text = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)