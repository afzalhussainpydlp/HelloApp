function sendMessage() {

    let input = document.getElementById("messageInput");
    let message = input.value;

    if (message.trim() === "") {
        return;
    }

    let csrfToken = document.querySelector(
        "[name=csrfmiddlewaretoken]"
    ).value;

    fetch("{% url 'send_message' %}", {
        method: "POST",
        headers: {
            "X-CSRFToken": csrfToken
        },
        body: new URLSearchParams({
            message: message,
            receiver_id:"{{receiver.id}}"
        })
    })
    .then(response => {
        console.log("Django response:", response.status);
        return response.json();
    })
    .then(data => {
        console.log("Django data:", data);
    })
    .catch(error => {
        console.error("Error:", error);
    });

    let messages = document.getElementById("messages");

    let newMessage = document.createElement("div");

    newMessage.className = "sent";
    newMessage.innerText = message;

    messages.appendChild(newMessage);

    input.value = "";
}


let input = document.getElementById("messageInput");

input.addEventListener("keydown", function(event) {

    if (event.key === "Enter") {
        sendMessage();
    }

});