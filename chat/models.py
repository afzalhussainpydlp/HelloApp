from django.db import models
from django.contrib.auth.models import User


class Message(models.Model):
    sender = models.CharField(max_length=100)

    sender_user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True
    )

    receiver_user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="received_messages",
        null=True,
        blank=True,
    )

    text = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    delivered_at = models.DateTimeField(null=True, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True)


# Indentation sahi kar diya - ab ye bilkul alag class hai
class UserStatus(models.Model):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="status"
    )
    last_seen = models.DateTimeField(null=True, blank=True)
    
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    phone = models.CharField(max_length=10, unique=True)
    full_name = models.CharField(max_length=25, blank=True)
    dp = models.ImageField(upload_to="dp/", blank=True, null=True)

    def __str__(self):
        return f"{self.full_name or self.user.username} ({self.phone})"


class PhoneOTP(models.Model):
    phone = models.CharField(max_length=10)
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    used = models.BooleanField(default=False)