import re

chat_html_path = r"d:\Kuliah\Magang\YDSF\Automatic Article\templates\chat.html"
css_out_path = r"d:\Kuliah\Magang\YDSF\Automatic Article\static\chat.css"
js_out_path = r"d:\Kuliah\Magang\YDSF\Automatic Article\static\js\chat.js"

with open(chat_html_path, "r", encoding="utf-8") as f:
    content = f.read()

# Extract CSS
style_match = re.search(r"<style>(.*?)</style>", content, re.DOTALL)
if style_match:
    css_content = style_match.group(1).strip()
    with open(css_out_path, "w", encoding="utf-8") as f:
        f.write("/* ── Chat Specific Styles ── */\n" + css_content + "\n")
    
    # Replace the style block
    content = content.replace(style_match.group(0), "<link rel=\"stylesheet\" href=\"{{ url_for('static', path='chat.css') }}\">")

# Extract JS
# The big script block is at the end. We'll find the last <script> tag.
script_blocks = list(re.finditer(r"<script>(.*?)</script>", content, re.DOTALL))
if script_blocks:
    last_script = script_blocks[-1]
    js_content = last_script.group(1).strip()
    with open(js_out_path, "w", encoding="utf-8") as f:
        f.write(js_content + "\n")
        
    content = content.replace(last_script.group(0), "<script src=\"{{ url_for('static', path='js/chat.js') }}\"></script>")

with open(chat_html_path, "w", encoding="utf-8") as f:
    f.write(content)

print("Extraction successful.")
