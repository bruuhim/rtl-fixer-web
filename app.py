from flask import Flask, render_template, request, send_file
import zipfile
import io

app = Flask(__name__)

def fix_rtl_content(content):
    # IMPORTANT: This is still a placeholder fix.
    # A real fix would require a library like `python-bidi` to process lines individually.
    return content[::-1]

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        files = request.files.getlist("files")
        if not files:
            return "No files uploaded", 400

        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for f in files:
                # --- START OF CHANGES ---
                content = None
                try:
                    # Try to decode with the most common encodings for subtitles
                    # utf-8-sig handles the BOM (Byte Order Mark) which is very common
                    content = f.read().decode('utf-8-sig')
                except UnicodeDecodeError:
                    try:
                        # Fallback to utf-16 if utf-8 fails
                        f.seek(0) # Reset file pointer before reading again
                        content = f.read().decode('utf-16')
                    except UnicodeDecodeError:
                        # If all else fails, skip this file or handle the error
                        # For now, we'll write an error message into the file.
                        content = f"Error: Could not decode the file '{f.filename}'. It may have an unsupported encoding."

                if content:
                    fixed_content = fix_rtl_content(content)
                    # We must encode the string back to bytes (using utf-8) before writing to the zip
                    zf.writestr(f.filename, fixed_content.encode('utf-8'))
                # --- END OF CHANGES ---

        memory_file.seek(0)
        return send_file(
            memory_file,
            download_name="fixed_files.zip",
            as_attachment=True,
            mimetype='application/zip'
        )
    return render_template("index.html")
