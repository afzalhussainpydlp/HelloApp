from django.shortcuts import render
from django.http import JsonResponse
from .models import Message


def home(request):
    return render(request, "index.html")


def chat_page(request):
    messages = Message.objects.all()

    return render(request, "chat.html", {
        "messages": messages
    })


def send_message(request):
    text = request.POST.get("message")

    Message.objects.create(
        sender="Afzal",
        text=text
    )

    return JsonResponse({
        "status": "success"
    })