from flask import Flask, render_template, request, send_file
import os
import zipfile
import io

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        files = request.files.getlist("files")
        if not files:
            return "No files uploaded", 400

        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for f in files:
                content = f.read().decode("utf-8")
                fixed_content = content[::-1]  # Replace with your RTL fix logic
                zf.writestr(f.filename, fixed_content)
        memory_file.seek(0)
        return send_file(
            memory_file,
            download_name="fixed_files.zip",
            as_attachment=True
        )
    return render_template("index.html")
