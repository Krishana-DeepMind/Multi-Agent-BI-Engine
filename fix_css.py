with open('frontend/src/app/globals.css', 'rb') as f:
    b = f.read()

text = b.decode('latin-1')
text = text.replace('\x97', '—').replace('\x96', '–')

with open('frontend/src/app/globals.css', 'w', encoding='utf-8') as f:
    f.write(text)

with open('frontend/src/app/globals.css', 'r', encoding='utf-8') as f:
    _ = f.read()

print("globals.css is now 100% valid UTF-8!")
