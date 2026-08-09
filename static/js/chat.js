function sendMessage() {

    let input = document.getElementById("messageInput");
    let message = input.value;

    if (message.trim() === "") {
        return;
    }

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