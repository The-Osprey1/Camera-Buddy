#!/bin/bash
# Downloads the three small face models face unlock uses (see desk_presence/faces.py)
# into /opt/desk-presence/models, and checks each one is exactly the file expected.
# Files already there and correct are skipped, so this is quick to run again.
#   sudo /opt/desk-presence/setup/get-models.sh
set -uo pipefail

DEST=/opt/desk-presence/models
if [ "$(id -u)" -ne 0 ]; then
    echo "Please run with sudo."
    exit 1
fi
mkdir -p "$DEST"

ZOO=https://media.githubusercontent.com/media/opencv/opencv_zoo/48b10fa505aeb51fbd127fc60a5c660040e823b7/models
SPOOF=https://raw.githubusercontent.com/suriAI/face-antispoof-onnx/2f8dd93dd0fd9d120f08e83d9020719f67ffdb68/models
# name, then where it comes from, then its SHA-256 fingerprint.
MODELS=(
    "face_detection_yunet_2023mar.onnx $ZOO/face_detection_yunet/face_detection_yunet_2023mar.onnx 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
    "face_recognition_sface_2021dec.onnx $ZOO/face_recognition_sface/face_recognition_sface_2021dec.onnx 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
    "face_antispoof_minifas.onnx $SPOOF/best/98.20/best_model.onnx af2381b88f38769222ed93379e12444e2a50814575de1c46170de570c55a42b6"
)

FAILED=no
for entry in "${MODELS[@]}"; do
    read -r name url sum <<<"$entry"
    if echo "$sum  $DEST/$name" | sha256sum -c --status 2>/dev/null; then
        continue
    fi
    echo "Downloading the face model $name..."
    if curl -fsSL --max-time 300 -o "$DEST/$name.part" "$url" &&
        echo "$sum  $DEST/$name.part" | sha256sum -c --status; then
        mv "$DEST/$name.part" "$DEST/$name"
    else
        rm -f "$DEST/$name.part"
        echo "Couldn't download $name (is the Pi online?). Face unlock needs it."
        FAILED=yes
    fi
done
chmod 644 "$DEST"/*.onnx 2>/dev/null
[ "$FAILED" = no ]
