from flask import Flask, request, send_file, render_template_string
import os
import zipfile
import re

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
OUTPUT_FOLDER = "fixed"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

U202B = "\u202B"

def fix_rtl_ass_line(line):
    if line.startswith("Dialogue:"):
        parts = line.split(",", 9)
        if len(parts) < 10:
            return line  # skip malformed lines

        text = parts[9]

        # Remove existing U+202B
        text = text.replace(U202B, "")

        # Prepend U+202B
        text = U202B + text

        # Fix line breaks
        text = re.sub(r'\\N', r'\\N' + U202B, text)
        text = re.sub(r'\\n', r'\\n' + U202B, text)

        # Handle braces
        text = text.replace("}", "}" + U202B)
        text = text.replace(U202B + "{", "{")

        parts[9] = text
        return ",".join(parts)
    else:
        return line  # leave non-dialogue lines untouched

def process_ass_file(filepath, filename):
    with open(filepath, "r", encoding="utf-8") as f:
        lines = f.readlines()

    fixed_lines = [fix_rtl_ass_line(line) for line in lines]

    fixed_path = os.path.join(OUTPUT_FOLDER, filename)
    with open(fixed_path, "w", encoding="utf-8") as f:
        f.writelines(fixed_lines)

    return fixed_path

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        files = request.files.getlist("files")
        zip_path = os.path.join(OUTPUT_FOLDER, "fixed_files.zip")
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for file in files:
                filepath = os.path.join(UPLOAD_FOLDER, file.filename)
                file.save(filepath)
                fixed_file = process_ass_file(filepath, file.filename)
                zipf.write(fixed_file, arcname=file.filename)
        return send_file(zip_path, as_attachment=True)

    return render_template_string("""
    <h2>Upload your ASS files</h2>
    <form method="post" enctype="multipart/form-data">
        <input type="file" name="files" multiple>
        <input type="submit" value="Fix RTL">
    </form>
    """)

if __name__ == "__main__":
    app.run(debug=True)
