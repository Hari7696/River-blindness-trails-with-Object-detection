import logging
import mmdet
import mmcv
import os
import torch
import urllib.request
import numpy as np
from mmcv import Config
from mmcv.ops import get_compiling_cuda_version, get_compiler_version
from mmcv.utils.logging import get_logger
from mmdet.apis import train_detector, set_random_seed
from mmdet.datasets import build_dataset
from mmdet.models import build_detector
import os
from Dataset import OnchoDataset
from mmdet.utils.memory import AvoidOOM
AvoidCUDAOOM = AvoidOOM()

def check_versions():
    logger = logging.getLogger(__name__)
    logger.info("Checking torch and mmdet config")
    logger.info("Torch version")
    logger.info(torch.__version__)
    logger.info("Torch sees CUDA?")
    logger.info(torch.cuda.is_available())
    logger.info("MMDet version")
    logger.info(mmdet.__version__)
    logger.info("Compiled CUDA version")
    logger.info(get_compiling_cuda_version())
    logger.info("CUDA Compiler version")
    logger.info(get_compiler_version())


def load_config(data_folder):
    base_file = "mmdetection/configs/faster_rcnn/faster_rcnn_r50_caffe_fpn_mstrain_1x_coco.py"
    cfg = Config.fromfile(base_file)
    
    # Modify dataset type and path
    cfg.dataset_type = 'OnchoDataset'           # This is a custom data loader script we created for the GDSC you can view it in src/Dataset.py
    cfg.data_root = data_folder

    # Modify dataset type and path
    cfg.dataset_type = 'OnchoDataset'           # This is a custom data loader script we created for the GDSC you can view it in src/Dataset.py
    cfg.data_root = data_folder

    cfg.data.train.type = 'OnchoDataset'
    cfg.data.train.data_root = data_folder      # path to the folder data
    cfg.data.train.img_prefix = 'jpgs/'         # path from data_root to the images folder
    cfg.data.train.ann_file = 'actual_train.csv'  # the file containing the train data labels

    cfg.data.test.type = 'OnchoDataset'
    cfg.data.test.data_root = data_folder
    cfg.data.test.img_prefix = 'jpgs/'
    cfg.data.test.ann_file = 'actual_test.csv'

    cfg.data.val.type = 'OnchoDataset'          # We will not use a separate validation data set in this tutorial, but we need to specify the values to overwrite the COCO defaults.
    cfg.data.val.data_root = data_folder
    cfg.data.val.img_prefix = 'jpgs/'
    cfg.data.val.ann_file = 'actual_test.csv'
    

    # Download weights


    # Set up working dir to save files and logs.
    model_dir = os.environ.get("SM_MODEL_DIR")
    if model_dir is None:        
        cfg.work_dir = f'{data_folder}/tutorial_exps/'
        #cfg.load_from = 'faster_rcnn_r101_caffe_fpn_mstrain_3x_coco_20210526_095742-a7ae426d.pth'
    else:
        cfg.work_dir = model_dir
        weights_url = 'https://download.openmmlab.com/mmdetection/v2.0/mask_rcnn/mask_rcnn_r50_caffe_fpn_mstrain-poly_3x_coco/mask_rcnn_r50_caffe_fpn_mstrain-poly_3x_coco_bbox_mAP-0.408__segm_mAP-0.37_20200504_163245-42aa3d00.pth'
        weights_path = weights_url.split('/')[-1]
        weights_path, headers = urllib.request.urlretrieve(weights_url, filename=weights_path)
        cfg.load_from = weights_path
    
    # Set seed thus the results are more reproducible
    # Set seed thus the results are more reproducible
    cfg.seed = 7
    set_random_seed(7, deterministic=False)
    cfg.gpu_ids = range(1)

    cfg.data.samples_per_gpu = 2 # These numbers will change depending on the size of your model and GPU.
    cfg.data.workers_per_gpu = 1 # These values are what we have found to be best for this model and GPU

    # modify number of classes of the model in box head
    cfg.model.roi_head.bbox_head.num_classes = 1  # a worm section is the only object we are detecting
    
    # The original learning rate (LR) is set for 8-GPU training.
    # We divide it by 8 since we only use one GPU and mulitply by the number of GPU workers.
#     cfg.auto_scale_lr.enable = True
#     cfg.auto_scale_lr.base_batch_size = cfg.data.samples_per_gpu
    cfg.runner.max_epochs = 24
    cfg.lr_config.step = [16, 23]
    cfg.optimizer.lr = 0.02 / 8 * cfg.data.workers_per_gpu
    cfg.lr_config.warmup = None
    cfg.log_config.interval = 50
    
    # Change the evaluation metric since we use customized dataset.
    cfg.evaluation.metric = 'mAP'
    # We can set the evaluation interval to reduce the evaluation times
    cfg.evaluation.interval = 1
    # We can set the checkpoint saving interval to reduce the storage cost
    cfg.checkpoint_config.interval = 1
    # How long do we want to train
    
    # set device to GPU
    cfg.device = "cuda"
    
    # Tutorial 5: Increase max number of sections, i.e., boxes
    cfg.model.test_cfg.rcnn.max_per_img = 400
    
    cfg.train_pipeline = [
        dict(type='LoadImageFromFile'),
        dict(type='LoadAnnotations', with_bbox=True),
        dict(
            type='Resize',
            img_scale=(5120, 5120), # Changed
            multiscale_mode='value',
            keep_ratio=True),
        dict(
            type='RandomCrop',
            crop_size=(0.3, 0.3),
            crop_type='relative', # Switched from relative_range to relative (fixed crops only)
            allow_negative_crop=True),
        dict(
            type='RandomFlip',
            flip_ratio=[0.5, 0.5], 
            direction=['horizontal', 'vertical']), 
        dict(
            type='Normalize',
            mean=[103.53, 116.28, 123.675],
            std=[1.0, 1.0, 1.0],
            to_rgb=False),
        dict(type='Pad', size_divisor=32),
        dict(type='DefaultFormatBundle'),
        dict(type='Collect', keys=['img', 'gt_bboxes', 'gt_labels'])
    ]
    cfg.data.train.pipeline = cfg.train_pipeline

    # Modify data pipeline (test-time augmentation)
    cfg.test_pipeline = [
        dict(type='LoadImageFromFile'),
        dict(
            type='MultiScaleFlipAug',
            img_scale=(5120, 5120), # Changed
            flip=False,
            transforms=[
                dict(type='Resize', keep_ratio=True),
                dict(type='RandomFlip'),
                dict(
                    type='Normalize',
                    mean=[103.53, 116.28, 123.675],
                    std=[1.0, 1.0, 1.0],
                    to_rgb=False),
                dict(type='Pad', size_divisor=32),
                dict(type='ImageToTensor', keys=['img']),
                dict(type='Collect', keys=['img'])
            ])
    ]
    cfg.data.test.pipeline = cfg.test_pipeline

    # Modify data pipeline (val-time augmentation)
    cfg.data.val.pipeline = [
        dict(type='LoadImageFromFile'),
        dict(
            type='MultiScaleFlipAug',
            img_scale=(5120, 5120), # Changed
            flip=False,
            transforms=[
                dict(type='Resize', keep_ratio=True),
                dict(type='RandomFlip'),
                dict(
                    type='Normalize',
                    mean=[103.53, 116.28, 123.675],
                    std=[1.0, 1.0, 1.0],
                    to_rgb=False),
                dict(type='Pad', size_divisor=32),
                dict(type='ImageToTensor', keys=['img']),
                dict(type='Collect', keys=['img'])
            ])
    ]
    
    

    cfg = Config.fromstring(cfg.pretty_text, '.py') # An ugly workaround to enable calling dict key by a dot (reconstruct object)
    
    return cfg, base_file

if __name__ == "__main__":
    logger = get_logger(__name__)
    
    check_versions()    
    data_folder = os.environ.get("SM_CHANNEL_TRAIN")
    
    import os 
    logger.info(os.getcwd())
    logger.info(os.listdir(data_folder))
   
    logger.info("Loading config")
    cfg, _ = load_config(data_folder)

    logging.info("Building dataset")
    datasets = [build_dataset(cfg.data.train)]

    logging.info("Building model")
    model = build_detector(cfg.model, train_cfg=cfg.get("train_cfg"), test_cfg=cfg.get("train_cfg"))
    model.CLASSES = datasets[0].CLASSES  # Add an attribute for visualization convenience
    
    logging.info("Training model")
    mmcv.mkdir_or_exist(os.path.abspath(cfg.work_dir))
    train_detector(model, datasets, cfg, validate=True)
    #output = AvoidCUDAOOM.retry_if_cuda_oom(train_detector)(model, datasets, cfg, validate=True)
