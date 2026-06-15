import re

with open('/Users/alex/Documents/EC2_Loki2_web.back/niceview/interface/interface.py', 'r') as f:
    text = f.read()

text = text.replace('create_leaflet_map', 'create_viv_viewer')

# Replace _, something_layer = ... with something_client, something_layer = ...
text = re.sub(r'_\s*,\s*([a-zA-Z0-9_]+)_layer\s*=\s*thor\.gis_client_and_layer', r'\1_client, \1_layer = thor.gis_client_and_layer', text)

# Replace globals block
text = re.sub(r'_\s*,\s*globals\(\)\[f"([a-zA-Z0-9_]+)_layer_\{selected_pathway\}"\]\s*=\s*thor\.gis_client_and_layer', 
              r'globals()[f"\1_client_{selected_pathway}"], globals()[f"\1_layer_{selected_pathway}"] = thor.gis_client_and_layer', text)

with open('/Users/alex/Documents/EC2_Loki2_web.back/niceview/interface/interface.py', 'w') as f:
    f.write(text)
print("done")
