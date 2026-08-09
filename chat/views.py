from django.shortcuts import render
from .models import Message


def home(request):
    return render(request, "index.html")


def chat_page(request):
    messages = Message.objects.all()

    return render(request, "chat.html", {
        "messages": messages
    })