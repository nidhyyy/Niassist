from flask import Flask, render_template, request, jsonify
from ai.gemini_client import ask_gemini

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/command", methods=["POST"])
def command():

    data = request.get_json()

    text = data.get("text")

    response = ask_gemini(text)

    return jsonify({
        "response": response
    })

if __name__ == "__main__":
    app.run(debug=True)