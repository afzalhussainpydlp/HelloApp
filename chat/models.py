from django.db import models


class Message(models.Model):
    sender = models.CharField(max_length=100)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)