from flask import Flask, render_template, request, send_file
import zipfile
import io

app = Flask(__name__)

def fix_rtl_content(original_content):
    """
    This function translates the logic from the user-provided Aegisub Lua script
    to correctly process .ass subtitle files for RTL text.
    """
    # The special 'Right-to-Left Embedding' Unicode character
    u202b = '\u202b'
    
    # Process the file line by line
    processed_lines = []
    for line in original_content.splitlines():
        # The fix should only apply to 'Dialogue' lines
        if line.strip().startswith('Dialogue:'):
            # Split the line into its 10 components.
            # The 10th component (index 9) is the actual subtitle text.
            parts = line.split(',', 9)
            if len(parts) == 10:
                text_portion = parts[9]
                
                # --- Start of Lua Script Logic Translation ---
                # 1. Remove all existing RTL characters to reset the line
                temp_text = text_portion.replace(u202b, '')
                
                # 2. Add the RTL character to the very beginning
                temp_text = u202b + temp_text
                
                # 3. Add the RTL character after hard and soft newlines
                temp_text = temp_text.replace('\\N', '\\N' + u202b)
                temp_text = temp_text.replace('\\n', '\\n' + u202b)
                
                # 4. Add the RTL character after a style block closing bracket
                temp_text = temp_text.replace('}', '}' + u202b)
                
                # 5. Clean up: remove the RTL character if it's right before a style block opening bracket
                temp_text = temp_text.replace(u202b + '{', '{')
                # --- End of Lua Script Logic Translation ---

                # Reassemble the dialogue line with the fixed text
                parts[9] = temp_text
                processed_lines.append(','.join(parts))
            else:
                # If the dialogue line is malformed, add it back as is
                processed_lines.append(line)
        else:
            # If the line is not a dialogue line (e.g., style, header), keep it unchanged
            processed_lines.append(line)
            
    # Join all the lines back together into a single string
    return '\n'.join(processed_lines)


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        files = request.files.getlist("files")
        if not files:
            return "No files uploaded", 400

        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w') as zf:
            for f in files:
                content = None
                try:
                    # Try to decode with common encodings
                    content = f.read().decode('utf-8-sig')
                except UnicodeDecodeError:
                    try:
                        f.seek(0)
                        content = f.read().decode('utf-16')
                    except UnicodeDecodeError:
                        content = f"Error: Could not decode file '{f.filename}'."

                if content:
                    # Apply the new, correct RTL fix logic
                    fixed_content = fix_rtl_content(content)
                    # Encode back to utf-8 before writing to the zip
                    zf.writestr(f.filename, fixed_content.encode('utf-8'))

        memory_file.seek(0)
        return send_file(
            memory_file,
            download_name="fixed_files.zip",
            as_attachment=True,
            mimetype='application/zip'
        )
    return render_template("index.html")
