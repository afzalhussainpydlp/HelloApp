from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login
from .models import Message


def home(request):

    if not request.user.is_authenticated:
        return redirect("login")

    users = User.objects.exclude(
        id=request.user.id
    )

    return render(request, "index.html", {
        "users": users
    })


def chat_page(request, user_id):

    if not request.user.is_authenticated:
        return redirect("login")

    receiver = get_object_or_404(
        User,
        id=user_id
    )

    messages = Message.objects.filter(
        sender_user=request.user,
        receiver_user=receiver
    ) | Message.objects.filter(
        sender_user=receiver,
        receiver_user=request.user
    )

    # Messages ko old → new order mein dikhayega
    messages = messages.order_by("created_at")

    return render(request, "chat.html", {
        "messages": messages,
        "receiver": receiver
    })


def send_message(request):

    if not request.user.is_authenticated:
        return JsonResponse({
            "status": "error",
            "message": "Login required"
        })

    if request.method == "POST":

        text = request.POST.get("message")
        receiver_id = request.POST.get("receiver_id")

        receiver = get_object_or_404(
            User,
            id=receiver_id
        )

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