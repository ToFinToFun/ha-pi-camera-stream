"""
Object Detector Module
======================

AI-baserad objektdetektering med stöd för:
- MobileNet SSD (snabb, fungerar på Raspberry Pi)
- YOLOv8 Nano (bättre precision, kräver mer CPU)
- Extern API (OpenAI Vision, Google Cloud Vision)

Kan filtrera falska larm genom att bara trigga på specifika objekt
(t.ex. personer, bilar, djur).
"""

import logging
import time
import numpy as np
from pathlib import Path

logger = logging.getLogger('detector')


# COCO-klasser (vanligaste)
COCO_CLASSES = [
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train',
    'truck', 'boat', 'traffic light', 'fire hydrant', 'stop sign',
    'parking meter', 'bench', 'bird', 'cat', 'dog', 'horse', 'sheep',
    'cow', 'elephant', 'bear', 'zebra', 'giraffe', 'backpack', 'umbrella',
    'handbag', 'tie', 'suitcase', 'frisbee', 'skis', 'snowboard',
    'sports ball', 'kite', 'baseball bat', 'baseball glove', 'skateboard',
    'surfboard', 'tennis racket', 'bottle', 'wine glass', 'cup', 'fork',
    'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich', 'orange',
    'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair',
    'couch', 'potted plant', 'bed', 'dining table', 'toilet', 'tv',
    'laptop', 'mouse', 'remote', 'keyboard', 'cell phone', 'microwave',
    'oven', 'toaster', 'sink', 'refrigerator', 'book', 'clock', 'vase',
    'scissors', 'teddy bear', 'hair drier', 'toothbrush',
]


class ObjectDetector:
    """
    Detekterar objekt i JPEG-frames.
    Konfigureras med vilka objekt som ska trigga events.
    """

    def __init__(self, config=None):
        config = config or {}

        self.enabled = config.get('enabled', False)
        self.backend = config.get('backend', 'mobilenet')  # mobilenet, yolov8, api
        self.confidence_threshold = config.get('confidence', 0.5)
        self.detect_classes = config.get('classes', ['person', 'car', 'dog', 'cat'])
        self.interval = config.get('interval', 2)  # Analysera var N:e sekund
        self.model = None
        self.last_detection_time = 0

        if not self.enabled:
            logger.info("Object detection disabled")
            return

        logger.info(f"Object detection: backend={self.backend}, classes={self.detect_classes}")

    def initialize(self):
        """Ladda modellen. Kallas separat för att inte blockera startup."""
        if not self.enabled:
            return

        try:
            if self.backend == 'mobilenet':
                self._init_mobilenet()
            elif self.backend == 'yolov8':
                self._init_yolov8()
            elif self.backend == 'api':
                logger.info("Using external API for detection")
            else:
                logger.error(f"Unknown backend: {self.backend}")
                self.enabled = False
        except Exception as e:
            logger.error(f"Failed to initialize detector: {e}")
            self.enabled = False

    def _init_mobilenet(self):
        """Initiera MobileNet SSD med OpenCV DNN."""
        import cv2

        # Sökvägar för modellen
        model_dir = Path(__file__).parent / 'models'
        model_dir.mkdir(exist_ok=True)

        prototxt = model_dir / 'MobileNetSSD_deploy.prototxt'
        caffemodel = model_dir / 'MobileNetSSD_deploy.caffemodel'

        if not prototxt.exists() or not caffemodel.exists():
            logger.info("Downloading MobileNet SSD model...")
            self._download_mobilenet(model_dir)

        self.model = cv2.dnn.readNetFromCaffe(str(prototxt), str(caffemodel))
        self.model_type = 'mobilenet'

        # MobileNet SSD klasser (VOC)
        self.mobilenet_classes = [
            'background', 'aeroplane', 'bicycle', 'bird', 'boat', 'bottle',
            'bus', 'car', 'cat', 'chair', 'cow', 'diningtable', 'dog',
            'horse', 'motorbike', 'person', 'pottedplant', 'sheep', 'sofa',
            'train', 'tvmonitor',
        ]

        logger.info("MobileNet SSD loaded successfully")

    def _download_mobilenet(self, model_dir):
        """Ladda ner MobileNet SSD modell-filer."""
        import urllib.request

        base_url = "https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master"
        prototxt_url = f"{base_url}/deploy.prototxt"
        caffemodel_url = "https://drive.google.com/uc?export=download&id=0B3gersZ2cHIxRm5PMWRoTkdHdHc"

        logger.info("Downloading prototxt...")
        urllib.request.urlretrieve(prototxt_url, model_dir / 'MobileNetSSD_deploy.prototxt')

        # Caffemodel kan vara svår att ladda ner automatiskt
        # Skapa en placeholder-fil med instruktioner
        readme = model_dir / 'README.md'
        readme.write_text(
            "# MobileNet SSD Model\n\n"
            "Download the model files:\n"
            "1. deploy.prototxt from: https://github.com/chuanqi305/MobileNet-SSD\n"
            "2. MobileNetSSD_deploy.caffemodel from the same repo\n\n"
            "Place both files in this directory.\n"
        )

        logger.warning(
            "MobileNet model files need to be downloaded manually. "
            "See pi-client/models/README.md for instructions."
        )

    def _init_yolov8(self):
        """Initiera YOLOv8 Nano."""
        try:
            from ultralytics import YOLO
            self.model = YOLO('yolov8n.pt')
            self.model_type = 'yolov8'
            logger.info("YOLOv8 Nano loaded successfully")
        except ImportError:
            logger.error("ultralytics not installed. Run: pip install ultralytics")
            self.enabled = False

    def detect(self, jpeg_data):
        """
        Detektera objekt i en JPEG-frame.

        Returnerar:
            list[dict] – Lista med detekterade objekt:
                class: str - Objektklass
                confidence: float - Konfidens (0-1)
                bbox: [x1, y1, x2, y2] - Bounding box (pixlar)
        """
        if not self.enabled or not self.model:
            return []

        # Rate limiting
        now = time.time()
        if now - self.last_detection_time < self.interval:
            return []
        self.last_detection_time = now

        try:
            if self.model_type == 'mobilenet':
                return self._detect_mobilenet(jpeg_data)
            elif self.model_type == 'yolov8':
                return self._detect_yolov8(jpeg_data)
        except Exception as e:
            logger.error(f"Detection error: {e}")
            return []

    def _detect_mobilenet(self, jpeg_data):
        """Kör MobileNet SSD detektion."""
        import cv2

        nparr = np.frombuffer(jpeg_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        h, w = img.shape[:2]
        blob = cv2.dnn.blobFromImage(img, 0.007843, (300, 300), 127.5)
        self.model.setInput(blob)
        detections = self.model.forward()

        results = []
        for i in range(detections.shape[2]):
            confidence = detections[0, 0, i, 2]
            if confidence < self.confidence_threshold:
                continue

            class_id = int(detections[0, 0, i, 1])
            if class_id >= len(self.mobilenet_classes):
                continue

            class_name = self.mobilenet_classes[class_id]

            # Mappa till standardnamn
            name_map = {'aeroplane': 'airplane', 'motorbike': 'motorcycle', 'tvmonitor': 'tv'}
            class_name = name_map.get(class_name, class_name)

            if class_name not in self.detect_classes:
                continue

            box = detections[0, 0, i, 3:7] * np.array([w, h, w, h])
            x1, y1, x2, y2 = box.astype(int)

            results.append({
                'class': class_name,
                'confidence': round(float(confidence), 3),
                'bbox': [int(x1), int(y1), int(x2), int(y2)],
            })

        return results

    def _detect_yolov8(self, jpeg_data):
        """Kör YOLOv8 detektion."""
        import cv2

        nparr = np.frombuffer(jpeg_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return []

        results = self.model(img, verbose=False, conf=self.confidence_threshold)
        detections = []

        for r in results:
            for box in r.boxes:
                class_id = int(box.cls[0])
                class_name = COCO_CLASSES[class_id] if class_id < len(COCO_CLASSES) else 'unknown'

                if class_name not in self.detect_classes:
                    continue

                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append({
                    'class': class_name,
                    'confidence': round(float(box.conf[0]), 3),
                    'bbox': [int(x1), int(y1), int(x2), int(y2)],
                })

        return detections

    def should_alert(self, detections):
        """Bestäm om detektionerna ska trigga en alert."""
        if not detections:
            return False

        # Filtrera på konfigurerade klasser
        relevant = [d for d in detections if d['class'] in self.detect_classes]
        return len(relevant) > 0
