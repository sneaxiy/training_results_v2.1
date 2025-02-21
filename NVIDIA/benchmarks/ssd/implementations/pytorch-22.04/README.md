<h1 align="center">Single Shot Detector (SSD)</h1>

- [Summary](#summary)
- [Running the benchmark](#running-the-benchmark)
  - [Requirements](#requirements)
  - [Building the docker image](#building-the-docker-image)
  - [Download dataset](#download-dataset)
  - [Download the pretrained backbone](#download-the-pretrained-backbone)
  - [Training on NVIDIA DGX-A100 (single node) with SLURM](#training-on-nvidia-dgx-a100-single-node-with-slurm)
  - [Training on NVIDIA DGX-A100 (multi node) with SLURM](#training-on-nvidia-dgx-a100-multi-node-with-slurm)
  - [Training on NVIDIA DGX-A100 (single node) with docker](#training-on-nvidia-dgx-a100-single-node-with-docker)
  - [Hyperparameter settings](#hyperparameter-settings)
- [Dataset/Environment](#datasetenvironment)
  - [Publication/Attribution](#publicationattribution)
  - [The MLPerf Subset](#the-mlperf-subset)
- [Model](#model)
  - [Backbone](#backbone)
  - [Weight and bias initialization](#weight-and-bias-initialization)
  - [Input augmentations](#input-augmentations)
  - [Publication/Attribution](#publicationattribution-1)
- [Quality](#quality)
  - [Quality metric](#quality-metric)
  - [Quality target](#quality-target)
  - [Evaluation frequency](#evaluation-frequency)
  - [Evaluation thoroughness](#evaluation-thoroughness)

# Summary
Single Shot MultiBox Detector (SSD) is an object detection network. For an
input image, the network outputs a set of bounding boxes around the detected
objects, along with their classes. For example:

![](https://upload.wikimedia.org/wikipedia/commons/3/38/Detected-with-YOLO--Schreibtisch-mit-Objekten.jpg)

SSD is a one-stage detector, both localization and classification are done in a
single pass of the network. This allows for a faster inference than region
proposal network (RPN) based networks, making it more suited for real time
applications like automotive and low power devices like mobile phones. This is
also sometimes referred to as being a "single shot" detector for inference.

# Running the benchmark
The benchmark is intended to run on NVIDIA GPUs, it is tested on A100 but other GPUs should work too (some optimization flags might not be available in all GPU generations).

## Requirements
The recommended way to run the benchmark is within docker containers. You need to setup your
machine with:
1. [PyTorch 22.04-py3 NGC container](https://ngc.nvidia.com/registry/nvidia-pytorch)
2. [Docker](https://docs.docker.com/engine/install/)
3. [NVIDIA container runtime](https://github.com/NVIDIA/nvidia-docker)
4. Slurm with [Pyxis](https://github.com/NVIDIA/pyxis) and [Enroot](https://github.com/NVIDIA/enroot) (multi-node)

## Building the docker image
Once the above requirements have been met, you can build the benchmark docker image with:
```bash
docker build --pull -t <DOCKER_REGISTRY>/mlperf-nvidia:single_stage_detector-pytorch .
docker push <DOCKER_REGISTRY>/mlperf-nvidia:single_stage_detector-pytorch
```

## Download dataset
The benchmark uses a subset of [OpenImages-v6](https://storage.googleapis.com/openimages/web/index.html).
To download the subset:
```bash
pip install fiftyone
cd ./public-scripts
./download_openimages_mlperf.sh -d <DATAPATH>
```

The script will download the benchmark subset with metadata and labels, then
convert the labels to [COCO](https://cocodataset.org/#home) format. The
downloaded dataset size is 352GB and the expected folder structure after
running the script is:
```
<DATAPATH>
│
└───info.json
│
└───train
│   └─── data
│   │      000002b66c9c498e.jpg
│   │      000002b97e5471a0.jpg
│   │      ...
│   └─── metadata
│   │      classes.csv
│   │      hierarchy.json
│   │      image_ids.csv
│   └─── labels
│          detections.csv
│          openimages-mlperf.json
│
└───validation
    └─── data
    │      0001eeaf4aed83f9.jpg
    │      0004886b7d043cfd.jpg
    │      ...
    └─── metadata
    │      classes.csv
    │      hierarchy.json
    │      image_ids.csv
    └─── labels
           detections.csv
           openimages-mlperf.json
```

Read more about the mlperf subset [here](#the-mlperf-subset).

## Download the pretrained backbone
The benchmark uses a ResNeXt50_32x4d backbone pretrained on ImageNet. The
weights are downloaded from PyTorch hub.

By default, the code will automatically download the weights to
`$TORCH_HOME/hub` (default is `~/.cache/torch/hub`) and save them for later use.

Alternatively, you can manually download the weights with:
```bash
bash ./public-scripts/download_backbone.sh
```

Then use the downloaded file with `--pretrained <PATH TO WEIGHTS>` .

## Training on NVIDIA DGX-A100 (single node) with SLURM
Launch configuration and system-specific hyperparameters for the NVIDIA DGX-A100
single node reference are in the `config_DGXA100_001x08x032.sh` script.

Steps required to launch single node training on NVIDIA DGX-A100:

```bash
source config_DGXA100_001x08x032.sh
CONT="<DOCKER_REGISTRY>/mlperf-nvidia:single_stage_detector-pytorch" DATADIR="<path/to/dir/containing/openimages/dir>" LOGDIR="<path/to/output/dir>" TORCH_HOME="<$(pwd) or path/to/pretrained/ckpt>" sbatch -N $DGXNNODES -t $WALLTIME run.sub
```

## Training on NVIDIA DGX-A100 (multi node) with SLURM
Launch configuration and system-specific hyperparameters for the NVIDIA DGX-A100
multi node reference are in the `config_DGXA100_*.sh` scripts.

Steps required to launch multi node training on NVIDIA DGX-A100:

```bash
source <MULT_NODE_CONFIG>
CONT="<DOCKER_REGISTRY>/mlperf-nvidia:single_stage_detector-pytorch" DATADIR="<path/to/dir/containing/openimages/dir>" LOGDIR="<path/to/output/dir>" TORCH_HOME="<$(pwd) or path/to/pretrained/ckpt>" sbatch -N $DGXNNODES -t $WALLTIME run.sub
```

## Training on NVIDIA DGX-A100 (single node) with docker
When generating results for the official v2.0 submission with one node, the
benchmark was launched onto a cluster managed by a SLURM scheduler. The
instructions in [Training on NVIDIA DGX-A100 (single node) with SLURM](#training-on-nvidia-dgx-a100-single-node-with-slurm) explain how that is done.

However, to make it easier to run this benchmark on a wider set of machine
environments, we are providing here an alternate set of launch instructions
that can be run using nvidia-docker. Note that performance or functionality may
vary from the tested SLURM instructions.

Launch configuration and system-specific hyperparameters for the NVIDIA DGX-A100
single node reference are in the `config_DGXA100_001x08x032.sh` script.

To launch single node training on NVIDIA DGX-A100 with docker, start
training with:

```bash
source config_DGXA100_001x08x032.sh
CONT="<DOCKER_REGISTRY>/mlperf-nvidia:single_stage_detector-pytorch" DATADIR="<path/to/dir/containing/openimages/dir>" LOGDIR="<path/to/output/dir>" TORCH_HOME="<$(pwd) or path/to/pretrained/ckpt>" ./run_with_docker.sh

```

Alternatively, you can launch an interactive docker session:

```bash
docker run --rm -it \
  --gpus=all \
  --ipc=host \
  -v <DATADIR>:/datasets/open-images-v6-mlperf \
  <DOCKER_REGISTRY>/mlperf-nvidia:single_stage_detector-pytorch bash
```

Then launching the training command manually with:

```bash
source config_DGXA100_001x08x032.sh
torchrun --standalone --nproc_per_node=${DGXNGPU} --no_python ./run_and_time.sh
```

You can read more about torchrun [here](https://pytorch.org/docs/stable/elastic/run.html).

## Hyperparameter settings
Hyperparameters are recorded in the `config_*.sh` files for each configuration
and in `run_and_time.sh`.

# Dataset/Environment
## Publication/Attribution
[Google Open Images Dataset V6](https://storage.googleapis.com/openimages/web/index.html)

## The MLPerf Subset
The MLPerf subset includes only 264 classes of the total 601 available in the
full dataset:

| Dataset           | # classes | # train images | # validation images | Size  |
|-------------------|-----------|----------------|---------------------|-------|
| OpenImages Full   | 601       | 1,743,042      | 41,620              | 534GB |
| OpenImages MLperf | 264       | 1,170,301      | 24,781              | 352GB |

These are the lowest level classes (no child classes) in the dataset
[semantic hierarchy tree](https://storage.googleapis.com/openimages/2018_04/bbox_labels_600_hierarchy_visualizer/circle.html)
with at least 1000 samples.

The list of used classes can be viewed
[here](https://github.com/mlcommons/training/blob/master/single_stage_detector/scripts/download_openimages_mlperf.sh).

# Model
This network takes an input 800x800 image from
[OpenImages-v6](https://storage.googleapis.com/openimages/web/index.html)
and 264 categories, and computes a set of bounding boxes and categories.
Other detector models use multiple stages, first proposing regions of interest
that might contain objects, then iterating over the regions of interest to try
to categorize each object. SSD does both of these in one stage, leading to
lower-latency and higher-performance inference.

## Backbone

The backbone is based on ResNeXt50_32x4d as described in Section 3 of
[this paper](https://arxiv.org/pdf/1611.05431.pdf).  Using the
same notation as Table 1 of the paper the backbone looks like:

| stage      | # stacked blocks | shape of a residual block  |
| :--------: | :--------------: | :------------------------: |
| conv1      |                  | 7x7, 64, stride 2          |
|            |                  | 3x3 max pool, stride 2     |
| conv2_x    | 3                | 1x1, 128                   |
|            |                  | 3x3, 128, groups=32        |
|            |                  | 1x1, 256                   |
| conv3_x    | 4                | 1x1, 256                   |
|            |                  | 3x3, 256, groups=32        |
|            |                  | 1x1, 512                   |
| conv4_x    | 6                | 1x1, 512                   |
|            |                  | 3x3, 512, groups=32        |
|            |                  | 1x1, 1024                  |
| conv5_x    | 3                | 1x1, 1024                  |
|            |                  | 3x3, 1024, groups=32       |
|            |                  | 1x1, 2048                  |

Input images are 800x800 RGB. They are fed to a 7x7 stride 2 convolution with
64 output channels, then through a 3x3 stride 2 max-pool layer.
The rest of the backbone is built from "building blocks": 3x3
grouped convolutions with a "short-cut" residual connection
around the pair.  All convolutions in the backbone are followed by batch-norm
and ReLU.

The backbone is initialized with the pretrained weights from the corresponding
layers of the ResNeXt50_32x4d implementation from the [Torchvision model
zoo](https://download.pytorch.org/models/-7cdf4587.pth), described in
detail [here](https://pytorch.org/hub/pytorch_vision_resnext/).  It is
a ResNeXt50_32x4d network trained on 224x224 ImageNet to achieve a Top-1
error rate of 22.38  and a Top-5 error rate of 6.30.

Of the five convolution stages, only the last three are trained.
The weights of the first two stages are frozen
([code](https://github.com/mlcommons/training/blob/master/single_stage_detector/ssd/model/backbone_utils.py#L94-L101)).
In addition, all batch norm layers in the backbone are frozen
([code](https://github.com/mlcommons/training/blob/master/single_stage_detector/ssd/model/backbone_utils.py#L52)).


## Weight and bias initialization
1. The ResNeXt50_32x4d backbone is initialized with the pretrained weights
   from [Torchvision model zoo](https://download.pytorch.org/models/-7cdf4587.pth).

2. The classification head weights are initialized using normal distribution
   with `mean=0` and `std=0.01`. The biases are initialized with zeros, except
   for the classification convolution which is initalized with
   `constant=-4.59511985013459`
   ([code](https://github.com/mlcommons/training/blob/master/single_stage_detector/ssd/model/retinanet.py#L85-L90)).

3. The regression head weights are initialized using normal distribution
   with `mean=0` and `std=0.01`. The biases are initialized with zeros
   ([code](https://github.com/mlcommons/training/blob/master/single_stage_detector/ssd/model/retinanet.py#L171-L177)).

4. The FPN network weights are initialized with uniform Kaiming (also known as
   He initialization) using `negative slope=1`. The biases are initialized
   with zeros
   ([code](https://github.com/mlcommons/training/blob/master/single_stage_detector/ssd/model/feature_pyramid_network.py#L90-L91)).


## Input augmentations
The input images are assumed to be sRGB with values in range 0.0 through 1.0.
The input pipeline does the following:

1. Random horizontal flip of both the image and its ground-truth bounding boxes
   with a probability of 50%.

2. Normalize the colors to a mean of (0.485, 0.456, 0.406) and standard
   deviation (0.229, 0.224, 0.225).

3. Resize image to 800x800 using bilinear interpolation.


## Publication/Attribution

Wei Liu, Dragomir Anguelov, Dumitru Erhan, Christian Szegedy, Scott Reed,
Cheng-Yang Fu, Alexander C. Berg.  [SSD: Single Shot MultiBox
Detector](https://arxiv.org/abs/1512.02325). In the _Proceedings of the
European Conference on Computer Vision_, (ECCV-14):21-37, 2016.

Kaiming He, Xiangyu Zhang, Shaoqing Ren, Jian Sun.  [Deep Residual Learning for
Image Recognition](https://arxiv.org/abs/1512.03385).  In the _Proceedings of
the Conference on Computer Vision and Pattern Recognition_, (CVPR):770-778, 2016.

Jonathan Huang, Vivek Rathod, Chen Sun, Menglong Zhu, Anoop Korattikara,
Alireza Fathi, Ian Fischer, Zbigniew Wojna, Yang Song, Sergio Guadarrama, Kevin
Murphy. [Speed/accuracy trade-offs for modern convolutional object
detectors](https://arxiv.org/abs/1611.10012).  In the _Proceedings of the
Conference on Computer Vision and Pattern Recognition_, (CVPR):3296-3305, 2017.

Krasin I., Duerig T., Alldrin N., Ferrari V., Abu-El-Haija S., Kuznetsova A.,
Rom H., Uijlings J., Popov S., Kamali S., Malloci M., Pont-Tuset J., Veit A.,
Belongie S., Gomes V., Gupta A., Sun C., Chechik G., Cai D., Feng Z.,
Narayanan D., Murphy K.
[OpenImages](https://storage.googleapis.com/openimages/web/index.html): A public
dataset for large-scale multi-label and multi-class image classification, 2017.

Saining Xie, Ross Girshick, Piotr Dollár, Zhuowen Tu, Kaiming He.
[Aggregated Residual Transformations for Deep Neural Networks](https://arxiv.org/abs/1611.05431)

Tsung-Yi Lin, Priya Goyal, Ross Girshick, Kaiming He, Piotr Dollár.
[Focal Loss for Dense Object Detection](https://arxiv.org/abs/1708.02002)

Torchvision pretrained [ResNeXt50_32x4d](https://pytorch.org/vision/0.12/models.html#id25) on ImageNet

Torchvision [RetinaNet](https://pytorch.org/vision/0.12/models.html#id65)

# Quality
## Quality metric
Metric is COCO box mAP (averaged over IoU of 0.5:0.95), computed over the
OpenImages-MLPerf validation subset.

## Quality target
mAP of 0.34

## Evaluation frequency
Every epoch, starting with the first one.

## Evaluation thoroughness
All the images in the OpenImages-MLPerf validation subset
