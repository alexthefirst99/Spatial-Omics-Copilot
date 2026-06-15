# Initialize conda for shell script
eval "$(conda shell.bash hook)"
conda activate loki2_env

MODEL="/condo/wanglab/shared/wxc/Dinov2/loki2_source_code/data/loki2_checkpoint.pth"

# INPUT file path from argument
FILE="$1"
if [ -z "$FILE" ]; then
    echo "Usage: ./loki2.sh <path_to_wsi.tif>"
    exit 1
fi

# Create output dir relative to input or standard
BASENAME=$(basename "$FILE" .tif)
OUTDIR="./outputs/loki_${BASENAME}"
mkdir -p "$OUTDIR"

WSI_PROPERTIES='{"slide_mpp": 0.25, "magnification": 40}'

echo "Processing ${FILE} -> ${OUTDIR}"
python /condo/wanglab/tmhtxt85/Project/Loki2_DrStevenLin/src/loki2/detect_cells.py \
  --model "$MODEL" \
  --outdir "$OUTDIR" \
  --geojson \
  --graph \
  process_wsi \
  --wsi_path "$FILE" \
  --wsi_properties "$WSI_PROPERTIES"