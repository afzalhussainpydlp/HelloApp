from django.shortcuts import render, get_object_or_404
from django.http import JsonResponse
from .models import Message
from django.contrib.auth import authenticate, login
from django.contrib.auth.models import User


def home(request):
    return render(request, "index.html")


def chat_page(request, user_id):

    print("CURRENT USER:", request.user)

    receiver = get_object_or_404(User, id=user_id)

    messages = Message.objects.filter(
        sender_user__in=[request.user, receiver],
        receiver_user__in=[request.user, receiver]
    ).order_by("created_at")

    return render(request, "chat.html", {
        "messages": messages,
        "receiver": receiver
    })


def send_message(request):

    if request.method == "POST":

        text = request.POST.get("message")
        receiver_id = request.POST.get("receiver_id")

        receiver = get_object_or_404(User, id=receiver_id)

        Message.objects.create(
            sender=request.user.username,
            sender_user=request.user,
            receiver_user=receiver,
            text=text
        )

        return JsonResponse({
            "status": "success"
        })

    return JsonResponse({
        "status": "error"
    })


def login_view(request):

    if request.method == "POST":

        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:

            login(request, user)

            return JsonResponse({
                "status": "success"
            })

        return JsonResponse({
            "status": "error",
            "message": "Invalid username or password"
        })

    return render(request, "login.html")