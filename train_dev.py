# -*- coding: utf-8 -*-
import argparse
import datetime
import os
import time
from torch.utils.tensorboard import SummaryWriter

os.environ['CUDA_VISIBLE_DEVICES'] = '0'

import numpy as np
import cv2
import matplotlib.pyplot as plt
import albumentations as albu
import torch
import segmentation_models_pytorch as smp
from segmentation_models_pytorch.utils import *
from torch.utils.data import DataLoader
from torch.utils.data import Dataset as BaseDataset

from tools.augmentation import *
from tools.datasets_VOC import Dataset_Train
import yaml

class Trainer():
    def __init__(self,args):
        self.args = args

        with open(args.model, 'r', encoding='utf-8') as f:
            yamlresult = yaml.load(f.read(), Loader=yaml.FullLoader)
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.dir = args.data_path
        self.encoder = yamlresult['encoder']
        self.encoder_weights = yamlresult['encoder_weights']
        self.classes = yamlresult['classes']
        self.activation = yamlresult['activation']
        self.model_name = yamlresult['model_name']

        self.model = self.create_model()
        self.preprocessing_fn = smp.encoders.get_preprocessing_fn(self.encoder, self.encoder_weights)
        self.loss = losses.DiceLoss()+losses.CrossEntropyLoss()
        self.metrics = [metrics.IoU(threshold=0.5),metrics.Recall()]
        # self.metrics = [metrics.IoU(threshold=0.5),metrics.Fscore(beta=1,threshold=0.5),metrics.Accuracy(threshold=0.5)]
        self.optimizer = torch.optim.Adam([dict(params=self.model.parameters(), lr=args.lr),])

        self.batch_size = args.batch_size
        self.epochs = args.epochs
        self.num_workers = args.num_workers

    def create_model(self):
        # create segmentation model with pretrained encoder
        if self.model_name == 'unet':
            model = smp.Unet(
                encoder_name=self.encoder,
                encoder_weights=self.encoder_weights,
                classes=len(self.classes),
                activation=self.activation,
            )
        # ???????????????????????????
        if self.args.pretrained:
            model = self.load_pretrained_model(model)
        return model
    # ?????????????????????
    def load_pretrained_model(self, model):
        checkpoint = torch.load(self.args.pretrained, map_location=self.device)
        model.load_state_dict(checkpoint['Unet'])
        print("Loaded pretrained model '{}'".format(self.args.pretrained))
        return model
    def dataload(self):
        # ?????????
        x_train_dir = os.path.join(self.dir, 'train')
        y_train_dir = os.path.join(self.dir, 'trainannot')

        # ?????????
        x_valid_dir = os.path.join(self.dir, 'val')
        y_valid_dir = os.path.join(self.dir, 'valannot')

        # ???????????????????????????
        train_dataset = Dataset_Train(
            x_train_dir,
            y_train_dir,
            images_size=self.args.base_size,
            augmentation=get_training_augmentation(base_size=self.args.base_size, crop_size=self.args.crop_size),
            preprocessing=get_preprocessing(self.preprocessing_fn),
            classes=self.classes,
        )

        valid_dataset = Dataset_Train(
            x_valid_dir,
            y_valid_dir,
            images_size=self.args.base_size,
            augmentation=get_validation_augmentation(base_size=self.args.base_size),
            preprocessing=get_preprocessing(self.preprocessing_fn),
            classes=self.classes,
        )

        # ?????????????????????????????????????????????
        train_loader = DataLoader(train_dataset, batch_size=self.batch_size, shuffle=True, num_workers=self.num_workers)
        valid_loader = DataLoader(valid_dataset, batch_size=1, shuffle=False, num_workers=self.num_workers)

        return train_loader,valid_loader

    def train_one_epoch(self):
        train_epoch = train.TrainEpoch(
            self.model,
            loss=self.loss,
            metrics=self.metrics,
            optimizer=self.optimizer,
            device=self.device,
            verbose=True,
        )
        return train_epoch

    def valid_one_epoch(self):
        valid_epoch = train.ValidEpoch(
            self.model,
            loss=self.loss,
            metrics=self.metrics,
            device=self.device,
            verbose=True,
            num_classes=len(self.classes),
        )
        return valid_epoch

    # ??????log?????????
    def create_folder(self):
        # ?????????????????????????????????????????????
        if not os.path.exists("logs"):
            os.mkdir("logs")
        # ????????????+??????????????????
        time_str = datetime.datetime.now().strftime("%m-%d %H_%M_%S-")
        log_dir = os.path.join("logs", time_str + self.model_name)
        if not os.path.exists(log_dir):
            os.mkdir(log_dir)
        self.results_file = log_dir + "/{}_results{}.txt".format(self.model_name, time_str)
        # ?????????tensborad
        self.tb = SummaryWriter(log_dir=log_dir)
        return log_dir

    def run(self):
        log_dir=self.create_folder()

        # ?????????????????????????????????????????????
        train_loader,valid_loader = self.dataload()

        # ??????????????????????????????????????????????????????
        train_epoch2 = self.train_one_epoch()
        valid_epoch2 = self.valid_one_epoch()

        max_score = 0
        start_time = time.time()
        for i in range(0, self.epochs):
            print('\nEpoch: {}'.format(i))
            train_logs = train_epoch2.run(train_loader)
            valid_logs,confmat = valid_epoch2.run(valid_loader)
            val_info = str(confmat)
            print(val_info)
            # ??????tb??????????????????????????????
            self.tb.add_scalar('loss', train_logs['dice_loss + cross_entropy_loss'], i)
            self.tb.add_scalar('iou_score', train_logs['iou_score'], i)
            self.tb.add_scalar('recall', train_logs['recall'], i)

            # self.tb.add_scalar('fscore', train_logs['fscore'], i)
            # self.tb.add_scalar('accuracy', train_logs['accuracy'], i)
            # self.tb.add_scalar('val_loss', valid_logs['dice_loss'], i)
            # self.tb.add_scalar('val_iou_score', valid_logs['iou_score'], i)
            # self.tb.add_scalar('val_fscore', valid_logs['fscore'], i)
            # self.tb.add_scalar('val_accuracy', valid_logs['accuracy'], i)
            self.tb.add_scalar('lr', self.optimizer.param_groups[0]['lr'], i)
            # ??????????????????????????????
            with open(self.results_file, "a") as f:
                f.write("Epoch: {} - \n".format(i))
                f.write("Train: {} - \n".format(train_logs))
                f.write("Valid: {} - \n".format(valid_logs))
                f.write("Confusion matrix: {} - \n".format(val_info))

            # do something (save model, change lr, etc.)
            if max_score < valid_logs['iou_score']:
                max_score = valid_logs['iou_score']
                torch.save(self.model, log_dir + '/best_model.pth')
                print('Model saved!')
            if i == 5:
                self.optimizer.param_groups[0]['lr'] = 1e-5
                print('Decrease decoder learning rate to 1e-5!')

        total_time = time.time() - start_time
        total_time_str = str(datetime.timedelta(seconds=int(total_time)))
        print("training total_time: {}".format(total_time_str))


def parse_args():
    parser = argparse.ArgumentParser(description="pytorch segnets training")
    # ??????
    parser.add_argument("--model", default=r"cfg/unet_cap_multi_res18.yaml", type=str, help="????????????,??????cfg?????????")
    parser.add_argument("--data-path", default=r'data/multi/data', help="VOCdevkit ??????")
    parser.add_argument("--batch-size", default=2, type=int,help="????????????")
    parser.add_argument("--base-size", default=[256, 256], type=int,help="??????????????????")
    parser.add_argument("--crop-size", default=[256, 256], type=int,help="??????????????????")
    parser.add_argument("--epochs", default=2, type=int, metavar="N",help="????????????")
    parser.add_argument("--num-workers", default=0, type=int, help="???????????????????????????")
    parser.add_argument('--lr', default=0.0001, type=float, help='???????????????')
    parser.add_argument("--pretrained", default=r"", type=str, help="?????????????????????")

    # ??????

    parser.add_argument('--resume', default=r"", help='????????????????????????????????????')
    parser.add_argument('--momentum', default=0.9, type=float, metavar='M',help='??????')
    parser.add_argument('--wd', '--weight-decay', default=1e-4, type=float,
                        metavar='W', help='????????????',dest='weight_decay')
    parser.add_argument('--optimizer', default='SGD', type=str, choices=['SGD', 'Adam', 'AdamW'], help='?????????')
    # ??????
    parser.add_argument('--open-tb', default=False, type=bool, help='??????tensorboard??????????????????')

    args = parser.parse_args()

    return args

# $# ?????????????????????
# ---------------------------------------------------------------
if __name__ == '__main__':

    # ????????????????????????
    args = parse_args()
    trainer = Trainer(args)
    trainer.run()
