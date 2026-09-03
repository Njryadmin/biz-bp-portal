import re
text = 'def test_x(client_with_auth):\n        r = client.get("/x")\n        assert r.status_code == 200\n'
# Use a simpler approach: just look for "client." anywhere
new_text, n = re.subn(r'\bclient\.', 'client_with_auth.', text)
print('replaced:', n)
print(repr(new_text))
