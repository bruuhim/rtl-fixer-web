from flask import Flask, render_template, request, send_file, jsonify, Response
import zipfile
import io
import re
import json
import os
import unicodedata
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size

# The special 'Right-to-Left Embedding' Unicode character
U202B = '\u202b'
U202C = '\u202c' # PDF (Pop Directional Formatting)

TASHKEEL = set([
    '\u064b', '\u064c', '\u064d', '\u064e', '\u064f', '\u0650', '\u0651', '\u0652', 
    '\u0653', '\u0654', '\u0655', '\u0656', '\u0657', '\u0658',
    '\u0610', '\u0611', '\u0612', '\u0613', '\u0614', '\u0615', '\u0616', '\u0617', 
    '\u0618', '\u0619', '\u061a',
    '\u06d6', '\u06d7', '\u06d8', '\u06d9', '\u06da', '\u06db', '\u06dc'
])

def remove_arabic_tashkeel(text):
    return ''.join(c for c in text if c not in TASHKEEL)

def remove_tags(text, keep_italic=False, keep_bold=False, keep_font_color=False):
    # Remove ASS override tags {...}
    text = re.sub(r'\{.*?\}', '', text)
    
    # Selectively remove HTML tags
    def replacer(match):
        tag = match.group(0)
        tag_lower = tag.lower()
        if keep_italic and (tag_lower == '<i>' or tag_lower == '</i>'):
            return tag
        if keep_bold and (tag_lower == '<b>' or tag_lower == '</b>'):
            return tag
        if keep_font_color and (tag_lower.startswith('<font') or tag_lower == '</font>'):
            return tag
        return ''
        
    text = re.sub(r'<[^>]+>', replacer, text)
    return text

def is_music_line(text):
    # Strip tags to check just the text content
    clean_text = re.sub(r'<[^>]+>', '', re.sub(r'\{.*?\}', '', text)).strip()
    return bool(re.match(r'^[\s♪♫♩♬\u266a\u266b\u266c\u266d~*\-\.]+$', clean_text))

def clean_brackets(text, opts):
    if not opts:
        return text
        
    try:
        options = json.loads(opts) if isinstance(opts, str) else opts
    except:
        return text

    if options.get('remove_square'):
        text = re.sub(r'\[.*?\]', '', text)
    if options.get('remove_round'):
        text = re.sub(r'\(.*?\)', '', text)
    if options.get('remove_angle'):
        # Only remove angle brackets if they aren't part of kept HTML tags
        # A simple approach: remove <...> if it doesn't look like a standard HTML formatting tag
        text = re.sub(r'<(?!\/?(?:i|b|font)[ >]).*?>', '', text)
    if options.get('remove_curly_text'):
        # This removes curly braces that were not removed by ASS tag removal (e.g. non-override curlies)
        text = re.sub(r'\{.*?\}', '', text)
        
    if 'custom_pairs' in options:
        for pair in options['custom_pairs']:
            if len(pair) == 2:
                o, c = re.escape(pair[0]), re.escape(pair[1])
                text = re.sub(f'{o}.*?{c}', '', text)
                
    return text

def ts_srt_to_ms(ts):
    parts = ts.split(':')
    if len(parts) == 3:
        h = int(parts[0])
        m = int(parts[1])
        s_ms = parts[2].split(',')
        s = int(s_ms[0])
        ms = int(s_ms[1]) if len(s_ms) > 1 else 0
        return h * 3600000 + m * 60000 + s * 1000 + ms
    return 0

def ms_to_ts_srt(ms):
    ms = max(0, ms)
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

def ts_ass_to_ms(ts):
    parts = ts.split(':')
    if len(parts) == 3:
        h = int(parts[0])
        m = int(parts[1])
        s_cs = parts[2].split('.')
        s = int(s_cs[0])
        cs = int(s_cs[1]) if len(s_cs) > 1 else 0
        return h * 3600000 + m * 60000 + s * 1000 + cs * 10
    return 0

def ms_to_ts_ass(ms):
    ms = max(0, ms)
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    cs = ms // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

def apply_text_filters(text, opts):
    if opts.get('remove_tashkeel'):
        text = remove_arabic_tashkeel(text)
    if opts.get('remove_all_tags'):
        text = remove_tags(text, opts.get('keep_italic'), opts.get('keep_bold'), opts.get('keep_font_color'))
    if opts.get('clean_brackets'):
        text = clean_brackets(text, opts.get('bracket_options'))
    if opts.get('fix_rtl'):
        if opts.get('fix_rtl_pdf'):
            text = U202B + text.replace(U202B, '').replace(U202C, '') + U202C
        else:
            text = U202B + text.replace(U202B, '')
    return text

def process_srt(content, opts):
    lines = content.splitlines()
    processed_lines = []
    
    timestamp_pattern = re.compile(r'^(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})$')
    
    time_shift_ms = opts.get('time_shift_ms', 0)
    from_ts = opts.get('time_shift_from', "00:00:00,000")
    from_ms = ts_srt_to_ms(from_ts) if time_shift_ms != 0 else 0
    
    i = 0
    block_index = 1
    while i < len(lines):
        line = lines[i]
        
        # Look for a number line followed by a timestamp line
        if line.strip().isdigit() and i + 1 < len(lines) and timestamp_pattern.match(lines[i + 1].strip()):
            ts_line = lines[i + 1].strip()
            match = timestamp_pattern.match(ts_line)
            start_ts, end_ts = match.groups()
            
            # Shift timestamps
            if time_shift_ms != 0:
                start_ms = ts_srt_to_ms(start_ts)
                end_ms = ts_srt_to_ms(end_ts)
                if start_ms >= from_ms:
                    start_ts = ms_to_ts_srt(start_ms + time_shift_ms)
                    end_ts = ms_to_ts_srt(end_ms + time_shift_ms)
            
            ts_line_modified = f"{start_ts} --> {end_ts}"
            
            i += 2
            
            # Read text lines
            text_lines = []
            while i < len(lines) and lines[i].strip() != '':
                text_lines.append(lines[i])
                i += 1
                
            joined_text = '\n'.join(text_lines)
            
            # Remove music lines if requested
            if opts.get('remove_music_lines') and is_music_line(joined_text):
                while i < len(lines) and lines[i].strip() == '':
                    i += 1
                continue
                
            # Apply text filters to each line
            filtered_lines = [apply_text_filters(tl, opts) for tl in text_lines]
            
            processed_lines.append(str(block_index))
            processed_lines.append(ts_line_modified)
            processed_lines.extend(filtered_lines)
            processed_lines.append("")
            block_index += 1
            
        elif line.strip() == '':
            # Extra empty lines can be skipped, we ensure one empty line after each block
            pass
        else:
            # Random non-conforming lines
            processed_lines.append(line)
        i += 1
        
    return '\n'.join(processed_lines)

def process_ass(content, opts):
    lines = content.splitlines()
    processed_lines = []
    
    time_shift_ms = opts.get('time_shift_ms', 0)
    from_ts = opts.get('time_shift_from', "0:00:00.00")
    from_ms = ts_ass_to_ms(from_ts) if time_shift_ms != 0 else 0
    
    for line in lines:
        if line.strip().startswith('Dialogue:'):
            parts = line.split(',', 9)
            if len(parts) == 10:
                start_ts, end_ts = parts[1], parts[2]
                
                if time_shift_ms != 0:
                    start_ms = ts_ass_to_ms(start_ts)
                    end_ms = ts_ass_to_ms(end_ts)
                    if start_ms >= from_ms:
                        parts[1] = ms_to_ts_ass(start_ms + time_shift_ms)
                        parts[2] = ms_to_ts_ass(end_ms + time_shift_ms)
                
                text_portion = parts[9]
                
                if opts.get('remove_music_lines') and is_music_line(text_portion.replace('\\N', '\n').replace('\\n', '\n')):
                    continue
                
                # Apply text filters. Note: ASS lines use \N or \n for breaks.
                # We split by \N or \n, apply filters, then join.
                break_pattern = re.compile(r'(\\[Nn])')
                sub_parts = break_pattern.split(text_portion)
                for j in range(0, len(sub_parts), 2):
                    sub_parts[j] = apply_text_filters(sub_parts[j], opts)
                
                parts[9] = ''.join(sub_parts)
                processed_lines.append(','.join(parts))
            else:
                processed_lines.append(line)
        else:
            processed_lines.append(line)
            
    return '\n'.join(processed_lines)

def convert_srt_to_ass(content):
    lines = content.splitlines()
    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "Collisions: Normal",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Default,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,0",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    
    timestamp_pattern = re.compile(r'^(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})$')
    
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().isdigit() and i + 1 < len(lines):
            ts_match = timestamp_pattern.match(lines[i + 1].strip())
            if ts_match:
                start_hms, start_ms, end_hms, end_ms = ts_match.groups()
                start_cs = int(start_ms) // 10
                end_cs = int(end_ms) // 10
                
                # ASS timestamp format: H:MM:SS.cc
                start_ts = f"{int(start_hms[:2])}:{start_hms[3:]}.{start_cs:02d}"
                end_ts = f"{int(end_hms[:2])}:{end_hms[3:]}.{end_cs:02d}"
                
                i += 2
                text_lines = []
                while i < len(lines) and lines[i].strip() != '':
                    text_lines.append(lines[i].strip())
                    i += 1
                
                text_joined = '\\N'.join(text_lines)
                ass_lines.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text_joined}")
                continue
        i += 1
    
    return '\n'.join(ass_lines)

def convert_ass_to_srt(content):
    lines = content.splitlines()
    srt_lines = []
    
    block_index = 1
    for line in lines:
        if line.strip().startswith('Dialogue:'):
            parts = line.split(',', 9)
            if len(parts) == 10:
                start_ts, end_ts = parts[1], parts[2]
                
                def ass_ts_to_srt(ts):
                    p = ts.split(':')
                    if len(p) == 3:
                        h = int(p[0])
                        m = int(p[1])
                        s_cs = p[2].split('.')
                        s = int(s_cs[0])
                        cs = int(s_cs[1]) if len(s_cs) > 1 else 0
                        return f"{h:02d}:{m:02d}:{s:02d},{cs*10:03d}"
                    return "00:00:00,000"
                
                start_srt = ass_ts_to_srt(start_ts)
                end_srt = ass_ts_to_srt(end_ts)
                
                text_portion = parts[9]
                text_portion = re.sub(r'\{.*?\}', '', text_portion) # Strip ASS overrides
                text_portion = text_portion.replace('\\N', '\n').replace('\\n', '\n')
                
                srt_lines.append(str(block_index))
                srt_lines.append(f"{start_srt} --> {end_srt}")
                srt_lines.append(text_portion)
                srt_lines.append("")
                block_index += 1
                
    return '\n'.join(srt_lines)

def convert_srt_to_vtt(content):
    lines = content.splitlines()
    vtt_lines = ["WEBVTT", ""]
    
    timestamp_pattern = re.compile(r'^(\d{2}:\d{2}:\d{2}),(\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}),(\d{3})$')
    
    for line in lines:
        match = timestamp_pattern.match(line.strip())
        if match:
            # Replace comma with dot
            vtt_lines.append(f"{match.group(1)}.{match.group(2)} --> {match.group(3)}.{match.group(4)}")
        else:
            vtt_lines.append(line)
            
    return '\n'.join(vtt_lines)

def convert_srt_to_lrc(content):
    lines = content.splitlines()
    lrc_lines = []
    
    timestamp_pattern = re.compile(r'^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s*-->')
    
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().isdigit() and i + 1 < len(lines):
            ts_match = timestamp_pattern.match(lines[i + 1].strip())
            if ts_match:
                h, m, s, ms = ts_match.groups()
                total_m = int(h) * 60 + int(m)
                cs = int(ms) // 10
                lrc_ts = f"[{total_m:02d}:{s}.{cs:02d}]"
                
                i += 2
                text_lines = []
                while i < len(lines) and lines[i].strip() != '':
                    text_lines.append(lines[i].strip())
                    i += 1
                
                text_joined = ' '.join(text_lines)
                lrc_lines.append(f"{lrc_ts}{text_joined}")
                continue
        i += 1
        
    return '\n'.join(lrc_lines)

def detect_encoding(content_bytes):
    try:
        return content_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        pass

    try:
        decoded = content_bytes.decode('utf-16')
        if len(decoded.strip()) > 0 and not all(ord(c) < 32 for c in decoded[:100]):
            return decoded
    except UnicodeDecodeError:
        pass

    try:
        return content_bytes.decode('utf-8')
    except UnicodeDecodeError:
        pass

    try:
        return content_bytes.decode('latin-1')
    except UnicodeDecodeError:
        pass

    raise UnicodeDecodeError("utf-8", content_bytes, 0, len(content_bytes), "Unable to decode file content")

def validate_srt_content(content):
    issues = []
    lines = content.splitlines()
    timestamp_pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$')

    if not lines:
        issues.append("File is empty")
        return issues

    if lines[0].startswith('\ufeff'):
        issues.append("File contains BOM marker")

    subtitle_blocks = 0
    for i, line in enumerate(lines):
        if line.strip().isdigit():
            subtitle_blocks += 1
            if i + 1 < len(lines) and timestamp_pattern.match(lines[i + 1]):
                continue
            else:
                issues.append(f"Invalid SRT structure around line {i + 1}")

    if subtitle_blocks == 0:
        issues.append("No valid subtitle blocks found")

    return issues

def preview_srt_content(content, max_lines=10):
    lines = content.splitlines()
    preview_lines = []
    timestamp_pattern = re.compile(r'^\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3}$')

    current_block = []
    for line in lines[:50]:
        if line.strip().isdigit() and len(current_block) == 0:
            current_block = [line]
        elif timestamp_pattern.match(line) and len(current_block) == 1:
            current_block.append(line)
        elif line.strip() == '' and len(current_block) == 2:
            current_block.append(line)
        elif len(current_block) == 3 and line.strip():
            text_line = line.strip()
            if text_line:
                has_rtl = any('\u0590' <= c <= '\u05FF' or '\u0600' <= c <= '\u06FF' or '\u0750' <= c <= '\u077F' for c in text_line)
                preview_lines.append({
                    'text': text_line[:100] + ('...' if len(text_line) > 100 else ''),
                    'has_rtl': has_rtl,
                    'is_rtl_fixed': U202B in text_line
                })
                if len(preview_lines) >= max_lines:
                    break
            current_block = []

    return preview_lines

@app.route("/preview", methods=["POST"])
def preview():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    try:
        content_bytes = file.read()
        content = detect_encoding(content_bytes)

        filename_lower = file.filename.lower()
        preview_data = {
            'filename': file.filename,
            'size': len(content_bytes),
            'encoding': 'utf-8-sig' if content_bytes.startswith(b'\xef\xbb\xbf') else 'utf-8',
            'issues': []
        }

        if filename_lower.endswith('.srt'):
            issues = validate_srt_content(content)
            preview_data['issues'] = issues
            preview_data['preview'] = preview_srt_content(content)
            preview_data['type'] = 'srt'
        elif filename_lower.endswith('.ass'):
            preview_data['preview'] = content.splitlines()[:10]
            preview_data['type'] = 'ass'
        else:
            return jsonify({'error': 'Unsupported file type'}), 400

        return jsonify(preview_data)

    except Exception as e:
        return jsonify({'error': f'Error processing file: {str(e)}'}), 500

@app.route("/process", methods=["POST"])
def process():
    files = request.files.getlist("files")
    if not files:
        return jsonify({'error': 'No files uploaded'}), 400
        
    opts = {
        'output_format': request.form.get('output_format', 'srt'),
        'input_encoding': request.form.get('input_encoding', 'auto'),
        'fix_rtl': request.form.get('fix_rtl', 'true').lower() == 'true',
        'fix_rtl_pdf': request.form.get('fix_rtl_pdf', 'false').lower() == 'true',
        'time_shift_ms': int(request.form.get('time_shift_ms', 0)),
        'time_shift_from': request.form.get('time_shift_from', "00:00:00,000" if request.form.get('output_format', 'srt') == 'srt' else "0:00:00.00"),
        'remove_music_lines': request.form.get('remove_music_lines', 'false').lower() == 'true',
        'remove_tashkeel': request.form.get('remove_tashkeel', 'false').lower() == 'true',
        'remove_all_tags': request.form.get('remove_all_tags', 'false').lower() == 'true',
        'keep_italic': request.form.get('keep_italic', 'false').lower() == 'true',
        'keep_bold': request.form.get('keep_bold', 'false').lower() == 'true',
        'keep_font_color': request.form.get('keep_font_color', 'false').lower() == 'true',
        'clean_brackets': request.form.get('clean_brackets', 'false').lower() == 'true',
        'bracket_options': request.form.get('bracket_options', '{}')
    }
    download_mode = request.form.get('download_mode', 'zip')
    
    if len(files) == 1:
        try:
            f = files[0]
            content_bytes = f.read()
            
            # Decode based on input_encoding
            if opts['input_encoding'] != 'auto':
                try:
                    content = content_bytes.decode(opts['input_encoding'])
                except Exception:
                    content = detect_encoding(content_bytes) # fallback
            else:
                content = detect_encoding(content_bytes)
                
            filename_lower = f.filename.lower()
            output_format = opts['output_format']
            
            # If the user asks for a specific output, process in original format first, then convert
            input_is_ass = filename_lower.endswith('.ass')
            
            if input_is_ass:
                fixed_content = process_ass(content, opts)
                if output_format == 'srt' or output_format == 'vtt' or output_format == 'lrc':
                    fixed_content = convert_ass_to_srt(fixed_content)
            else:
                fixed_content = process_srt(content, opts)
                if output_format == 'ass':
                    fixed_content = convert_srt_to_ass(fixed_content)
                    
            if output_format == 'vtt':
                fixed_content = convert_srt_to_vtt(fixed_content)
            elif output_format == 'lrc':
                fixed_content = convert_srt_to_lrc(fixed_content)
                
            # Determine output filename
            base_name = os.path.splitext(f.filename)[0]
            if not base_name:
                base_name = "Subtitle"
            ext_map = {'srt': '.srt', 'ass': '.ass', 'vtt': '.vtt', 'lrc': '.lrc'}
            out_ext = ext_map.get(output_format, '.srt')
            out_filename = f"{base_name}_Fixed.by.@bruuhim{out_ext}"
            
            # Encode output
            if out_ext in ['.srt', '.ass']:
                out_bytes = fixed_content.encode('utf-8-sig') # with BOM
            else:
                out_bytes = fixed_content.encode('utf-8')
                
            import urllib.parse
            encoded_name = urllib.parse.quote(out_filename)
            return Response(
                out_bytes,
                mimetype="text/plain",
                headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"}
            )
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            try:
                content_bytes = f.read()
                
                if opts['input_encoding'] != 'auto':
                    try:
                        content = content_bytes.decode(opts['input_encoding'])
                    except Exception:
                        content = detect_encoding(content_bytes)
                else:
                    content = detect_encoding(content_bytes)

                filename_lower = f.filename.lower()
                output_format = opts['output_format']
                
                input_is_ass = filename_lower.endswith('.ass')
                
                if input_is_ass:
                    fixed_content = process_ass(content, opts)
                    if output_format == 'srt' or output_format == 'vtt' or output_format == 'lrc':
                        fixed_content = convert_ass_to_srt(fixed_content)
                else:
                    fixed_content = process_srt(content, opts)
                    if output_format == 'ass':
                        fixed_content = convert_srt_to_ass(fixed_content)
                        
                if output_format == 'vtt':
                    fixed_content = convert_srt_to_vtt(fixed_content)
                elif output_format == 'lrc':
                    fixed_content = convert_srt_to_lrc(fixed_content)

                base_name = os.path.splitext(f.filename)[0]
                if not base_name:
                    base_name = f"Subtitle_{files.index(f) + 1}"
                ext_map = {'srt': '.srt', 'ass': '.ass', 'vtt': '.vtt', 'lrc': '.lrc'}
                out_ext = ext_map.get(output_format, '.srt')
                out_filename = f"{base_name}_Fixed.by.@bruuhim{out_ext}"

                if out_ext in ['.srt', '.ass']:
                    out_bytes = fixed_content.encode('utf-8-sig')
                else:
                    out_bytes = fixed_content.encode('utf-8')

                zf.writestr(out_filename, out_bytes)
            except Exception as e:
                pass # Continue processing other files

    memory_file.seek(0)
    
    first_name = os.path.splitext(files[0].filename)[0] if files and files[0].filename else "Subtitles"
    if not first_name:
        first_name = "Subtitle"
    more = len(files) - 1
    if more <= 0:
        zip_name = f"{first_name}_Fixed.by.@bruuhim.zip"
    else:
        zip_name = f"{first_name}_and_{more}_more_Fixed.by.@bruuhim.zip"

    return send_file(
        memory_file,
        download_name=zip_name,
        as_attachment=True,
        mimetype='application/zip'
    )

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        return process()
    return render_template("index.html")
