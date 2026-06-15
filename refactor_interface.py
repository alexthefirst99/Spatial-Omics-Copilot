import re
import os

filepath = '/Users/alex/Documents/EC2_Loki2_web.back/niceview/interface/interface.py'
with open(filepath, 'r') as f:
    text = f.read()

# 1. create_leaflet_map -> create_viv_viewer
text = text.replace('create_leaflet_map', 'create_viv_viewer')

# 2. Add client to all _ assignments
text = re.sub(r'_\s*,\s*([a-zA-Z0-9_]+)_layer\s*=\s*thor\.gis_client_and_layer', r'\1_client, \1_layer = thor.gis_client_and_layer', text)

# 3. For globals block:
text = re.sub(r'_\s*,\s*globals\(\)\[f"(.+?)_layer_\{selected_pathway\}"\]\s*=\s*thor\.gis_client_and_layer', 
              r'globals()[f"\1_client_{selected_pathway}"], globals()[f"\1_layer_{selected_pathway}"] = thor.gis_client_and_layer', text)

# 4. Replace (_layer occurrences in lists or arrays with _client
text = re.sub(r'\[\(([a-zA-Z0-9_]+)_layer,\s*(.+?)\)\]', r'[(\1_client, \2)]', text)

# 5. Fix all_pathway_layer append
text = text.replace('all_pathway_layer.append((globals()[f"cell_pathway_heatmap_layer_{selected_pathway}"], f\'{selected_pathway}\'))', 
                    'all_pathway_layer.append((globals()[f"cell_pathway_heatmap_client_{selected_pathway}"], f\'{selected_pathway}\'))')

# 6. Some arrays might have multiple layers, let's catch standard tuples:
# Just replace any remaining `_layer,` arguments inside create_viv_viewer calls where it implies a client is needed.
# Let's ensure the list_of_layers parameter name is consistent, but that's fine.

with open(filepath, 'w') as f:
    f.write(text)

print("Refactor complete.")
