# Copyright (c) 2018-2022, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import warnings
from nvidia import dali
from nvidia.dali.pipeline import Pipeline
import nvidia.dali.ops as ops
import nvidia.dali.types as types
from nvidia.dali.plugin.mxnet import DALIClassificationIterator, LastBatchPolicy
import horovod.mxnet as hvd
from mlperf_logging.mllog import constants
from mlperf_log_utils import mx_resnet_print_event
from packaging import version
import os
import tempfile


def add_dali_args(parser):

    group = parser.add_argument_group('DALI', 'pipeline and augumentation')
    group.add_argument('--e2e-cuda-graphs', type=int, help='fwd+bwd+update capture')
    group.add_argument('--data-train', type=str, help='the training data')
    group.add_argument('--data-train-idx', type=str, default='', help='the index of training data')
    group.add_argument('--data-val', type=str, help='the validation data')
    group.add_argument('--data-val-idx', type=str, default='', help='the index of validation data')
    group.add_argument('--use-dali', action='store_true',
                       help='use dalli pipeline and augunetation')
    group.add_argument('--max-random-area', type=float, default=1,
                       help='max area to crop in random resized crop, whose range is [0, 1]')
    group.add_argument('--min-random-area', type=float, default=1,
                       help='min area to crop in random resized crop, whose range is [0, 1]')
    parser.add_argument('--input-batch-multiplier', type=int, default=1,
                        help="use larger batches for input pipeline")
    group.add_argument('--separ-val', action='store_true',
                       help='each process will perform independent validation on whole val-set')
    group.add_argument('--min-random-aspect-ratio', type=float, default=3./4.,
                       help='min value of aspect ratio, whose value is either None or a positive value.')
    group.add_argument('--max-random-aspect-ratio', type=float, default=4./3.,
                       help='max value of aspect ratio. If min_random_aspect_ratio is None, '
                            'the aspect ratio range is [1-max_random_aspect_ratio, '
                            '1+max_random_aspect_ratio], otherwise it is '
                            '[min_random_aspect_ratio, max_random_aspect_ratio].')
    group.add_argument('--dali-threads', type=int, default=3, help="number of threads" +\
                       "per GPU for DALI")
    group.add_argument('--image-shape', type=str,
                       help='the image shape feed into the network, e.g. (3,224,224)')
    group.add_argument('--num-examples', type=int, help='the number of training examples')
    group.add_argument('--num-val-examples', type=int, default=-1, help='the number of validation examples')
    group.add_argument('--dali-dont-use-mmap', type=int, default=1, help="DALI doesn't use mmap")
    group.add_argument('--dali-bytes-per-sample-hint', type=int, default=10485760, help="DALI preallocate memory")
    group.add_argument('--dali-tmp-buffer-hint', type=int, default=25273239, help="DALI tmp-buffer-hint")
    group.add_argument('--dali-decoder-buffer-hint', type=int, default=1315942, help="DALI decoder-buffer-hint")
    group.add_argument('--dali-crop-buffer-hint', type=int, default=165581, help="DALI crop-buffer-hint")
    group.add_argument('--dali-normalize-buffer-hint', type=int, default=441549, help="DALI normalize-buffer-hint")
    group.add_argument('--dali-hw-decoder-load', type=float, default=0, help="Using H/W Jpeg Decode")
    group.add_argument('--dali-prefetch-queue', type=int, default=3, help="DALI prefetch queue depth")
    group.add_argument('--dali-nvjpeg-memory-padding', type=int, default=16, help="Memory padding value for nvJPEG (in MB)")
    group.add_argument('--dali-preallocate-width', type=int, default=0,
                       help="Image width hint to preallocate memory for the HW JPEG decoder")
    group.add_argument('--dali-preallocate-height', type=int, default=0,
                       help="Image height hint to preallocate memory for the HW JPEG decoder")
    group.add_argument('--dali-roi-decode', default=False, type=lambda x: (str(x).lower() in ['true', '1', 'yes']),
                       help='use ROI decode, available starting in DALI 0.8')
    group.add_argument('--dali-cache-size', type=int, default=0,
                       help='Cache decoded images with static shards with the specified cache size '
                            ' (in MB), available starting in DALI 0.8')
    group.add_argument('--lazy_init_sanity', action='store_true',
                       help='makes sure that data is not touched during the lazy init, '
                       'user need to clean up /tmp from symlinks created there')

    return parser


_mean_pixel = [255 * x for x in (0.485, 0.456, 0.406)]
_std_pixel  = [255 * x for x in (0.229, 0.224, 0.225)]


def dali_reader_op(cache_size,  path, index_path, dont_use_mmap,
        shard_id, num_shards, pad_last_batch, random_shuffle, stick_to_shard=False,
        lazy_init=True, skip_cached_images=False):

    if cache_size > 0:
        stick_to_shard=True
        skip_cached_images=True

    return ops.MXNetReader(path = path,
                           index_path = index_path,
                           dont_use_mmap = dont_use_mmap,
                           random_shuffle = random_shuffle,
                           shard_id = shard_id,
                           num_shards = num_shards,
                           stick_to_shard = stick_to_shard,
                           lazy_init = lazy_init,
                           skip_cached_images = skip_cached_images,
                           pad_last_batch = pad_last_batch)

def dali_image_decoder_op(cache_size, hw_decoder_load, device_memory_padding,
                          host_memory_padding, preallocate_width_hint,
                          preallocate_height_hint, bytes_per_sample_hint,
                          cache_threshold=None, cache_type=None,
                          cache_debug=None):

    if cache_size > 0:
         cache_type='largest'
         cache_threshold=0
         cache_debug=False

    return ops.ImageDecoder(device="mixed",
                            output_type = types.RGB,
                            hw_decoder_load = hw_decoder_load,
                            device_memory_padding = device_memory_padding,
                            host_memory_padding = host_memory_padding,
                            cache_type = cache_type,
                            cache_size = cache_size,
                            cache_threshold = cache_threshold,
                            preallocate_width_hint = preallocate_width_hint,
                            preallocate_height_hint =  preallocate_height_hint,
                            cache_debug = cache_debug,
                            bytes_per_sample_hint = bytes_per_sample_hint,
                            affine = False)

def dali_crop_mirror_normalize_op(dtype, output_layout, crop_shape, pad_output, mean, bytes_per_sample_hint, std):
    return ops.CropMirrorNormalize(device = "gpu",
                                   dtype = types.FLOAT16 if dtype == 'float16' else types.FLOAT,
                                   output_layout = output_layout,
                                   crop = crop_shape,
                                   pad_output = pad_output,
                                   mean = mean,
                                   bytes_per_sample_hint = bytes_per_sample_hint,
                                   std = std)


''' 
https://docs.nvidia.com/deeplearning/dali/user-guide/docs/plugins/mxnet_plugin_api.html

If reader_name is set, DALIClassificationIterator queries the shard_size, number_of_shards,
and all all other properties necessary to count properly the number of relevant and padded
samples that iterator needs to deal with. It automatically sets last_batch_padded accordingly
to match the reader’s configuration

For Training, we set:
    last_batch_policy = LastBatchPolicy.FILL
    last_batch_padded = False

    If batch size is not perfect multiple of shard_size, DALI fills the last batch with samples from next epoch.
    Example:
    With the data set [1, 2, 3, 4, 5, 6, 7] and the batch size 2:
    last batch = [7, 1] and MXNet array property .pad=0, next iteration will return [2, 3]


For Evaluation, we set:
    last_batch_policy = LastBatchPolicy.PARTIAL
    last_batch_padded = True

    If batch size is not perfect multiple of shard_size, DALI fills the last batch with repeat samples and sets
    MxNet property of ".pad=1", MxNet drops the validation score from these samples.
    Example:
    With the data set [1, 2, 3, 4, 5, 6, 7] and the batch size 2:
    last batch = [7, 7] and MXNet array property .pad=1, next iteration will return [1, 2]

'''

def get_dali_classification_iterator(pipes, last_batch_policy, last_batch_padded, size, reader_name, num_workers):
    if num_workers == 1:
        reader_name = None
    else:
        last_batch_padded = False  # gets overriden from the Reader 
        size = -1                  # gets overriden from the Reader

    return DALIClassificationIterator(pipes,
                                      last_batch_policy=last_batch_policy,
                                      last_batch_padded=last_batch_padded,
                                      size=size,
                                      reader_name=reader_name)


class HybridTrainPipe(Pipeline):
    '''
        700GB of data set whcih has 1.29M images ~= 600kb per image. Rounding up to 1MB = 1048576
    '''
    def __init__(self, batch_size, num_threads, device_id, rec_path, idx_path,
                 shard_id, num_shards, crop_shape,
                 min_random_area, max_random_area,
                 min_random_aspect_ratio, max_random_aspect_ratio,
                 nvjpeg_padding, hw_decoder_load, dont_use_mmap, prefetch_queue=3,
                 seed=12,
                 output_layout=types.NCHW, pad_output=True, dtype='float16',
                 mlperf_print=True, use_roi_decode=False,
                 preallocate_width_hint=0, preallocate_height_hint=0,
                 decoder_buffer_hint=1315942,crop_buffer_hint=165581,normalize_buffer_hint=441549,tmp_buffer_hint=25273239,
                 cache_size=0):
        super(HybridTrainPipe, self).__init__(
                batch_size, num_threads, device_id,
                seed = seed + device_id,
                prefetch_queue_depth = prefetch_queue)

        self.input = dali_reader_op(cache_size=cache_size, path=[rec_path], index_path=[idx_path],
                                         dont_use_mmap=dont_use_mmap, shard_id=shard_id, num_shards=num_shards,
                                         pad_last_batch=False, random_shuffle=True)

        if use_roi_decode and cache_size == 0:
            assert (version.parse(dali.__version__) >= version.parse("1.2.0") ),"Upgrade DALI version to >= 1.2.0!"
            self.decode = ops.ImageDecoderRandomCrop(device = "mixed", output_type = types.RGB,
                                                 device_memory_padding = nvjpeg_padding,
                                                 host_memory_padding = nvjpeg_padding,
                                                 random_area = [
                                                      min_random_area,
                                                      max_random_area],
                                                 random_aspect_ratio = [
                                                      min_random_aspect_ratio,
                                                      max_random_aspect_ratio],
                                                 hw_decoder_load=hw_decoder_load,
                                                 preallocate_width_hint=preallocate_width_hint,
                                                 preallocate_height_hint=preallocate_height_hint,
                                                 bytes_per_sample_hint=decoder_buffer_hint,
                                                 affine = False)
            self.rrc = ops.Resize(device = "gpu", resize_x=crop_shape[0],
                              resize_y=crop_shape[1],
                              bytes_per_sample_hint=decoder_buffer_hint
                              )
        else:
            self.decode = dali_image_decoder_op(hw_decoder_load=hw_decoder_load,
                                                device_memory_padding=nvjpeg_padding,
                                                host_memory_padding=nvjpeg_padding,
                                                cache_size=cache_size,
                                                preallocate_width_hint=preallocate_width_hint,
                                                preallocate_height_hint=preallocate_height_hint,
                                                bytes_per_sample_hint=decoder_buffer_hint)

            self.rrc = ops.RandomResizedCrop(device = "gpu",
                                             random_area = [
                                                 min_random_area,
                                                 max_random_area],
                                             random_aspect_ratio = [
                                                 min_random_aspect_ratio,
                                                 max_random_aspect_ratio],
                                             bytes_per_sample_hint=crop_buffer_hint,
                                             temp_buffer_hint=tmp_buffer_hint,
                                             size = crop_shape)

        self.cmnp = dali_crop_mirror_normalize_op(dtype=dtype, output_layout=output_layout,
                                                  crop_shape = crop_shape,
                                                  pad_output = pad_output,
                                                  mean = _mean_pixel,
                                                  bytes_per_sample_hint=normalize_buffer_hint,
                                                  std=_std_pixel)

        self.coin = ops.CoinFlip(probability = 0.5)


    def define_graph(self):
        rng = self.coin()
        self.jpegs, self.labels = self.input(name = "Reader")

        images = self.decode(self.jpegs)
        images = self.rrc(images)
        output = self.cmnp(images, mirror = rng)
        return [output, self.labels]


class HybridValPipe(Pipeline):
    def __init__(self, batch_size, num_threads, device_id, rec_path, idx_path,
                 shard_id, num_shards, crop_shape,
                 nvjpeg_padding, hw_decoder_load, dont_use_mmap, prefetch_queue=3,
                 seed=12, resize_shp=None,
                 output_layout=types.NCHW, pad_output=True, dtype='float16',
                 mlperf_print=True,
                 preallocate_width_hint=0, preallocate_height_hint=0,
                 decoder_buffer_hint=1315942, crop_buffer_hint=165581,
                 normalize_buffer_hint=441549, tmp_buffer_hint=25273239,
                 cache_size=0):

        super(HybridValPipe, self).__init__(
                batch_size, num_threads, device_id,
                seed = seed + device_id,
                prefetch_queue_depth = prefetch_queue)

        self.input = dali_reader_op(cache_size = cache_size, path = [rec_path], index_path = [idx_path],
                                         dont_use_mmap = dont_use_mmap, shard_id = shard_id,
                                         num_shards = num_shards, pad_last_batch=True, random_shuffle=False)

        self.decode = dali_image_decoder_op(hw_decoder_load=hw_decoder_load,
                                                device_memory_padding=nvjpeg_padding,
                                                host_memory_padding=nvjpeg_padding,
                                                cache_size=cache_size,
                                                preallocate_width_hint=preallocate_width_hint,
                                                preallocate_height_hint=preallocate_height_hint,
                                                bytes_per_sample_hint=decoder_buffer_hint)

        self.resize = ops.Resize(device = "gpu", resize_shorter=resize_shp,
                                             bytes_per_sample_hint=crop_buffer_hint,
                                             temp_buffer_hint=tmp_buffer_hint) if resize_shp else None

        self.cmnp = dali_crop_mirror_normalize_op(dtype=dtype, output_layout=output_layout,
                                                  crop_shape = crop_shape,
                                                  pad_output = pad_output,
                                                  mean = _mean_pixel,
                                                  bytes_per_sample_hint=normalize_buffer_hint,
                                                  std=_std_pixel)

    def define_graph(self):
        self.jpegs, self.labels = self.input(name = "Reader")
        images = self.decode(self.jpegs)
        if self.resize:
            images = self.resize(images)
        output = self.cmnp(images)
        return [output, self.labels]


def _get_rank_and_worker_count(args, kv):
    if 'horovod' in args.kv_store:
        rank = hvd.rank()
        num_workers = hvd.size()
    else:
        rank = kv.rank if kv else 0
        num_workers = kv.num_workers if kv else 1
    return (rank, num_workers)


def link_to_tmp_file(src, dst):
    # tempfile.mkstemp will create a file with _dali at the end
    # so when we add _tmp is will be still unique
    tmp = dst + '_tmp'
    os.symlink(src, tmp)
    os.rename(tmp, dst)


def get_tmp_file():
    fd, path = tempfile.mkstemp(suffix='_dali')
    os.close(fd)
    return path


def build_input_pipeline(args, kv=None):
    # resize is default base length of shorter edge for dataset;
    # all images will be reshaped to this size
    resize = int(args.resize)
    # target shape is final shape of images pipelined to network;
    # all images will be cropped to this size
    target_shape = tuple([int(l) for l in args.image_shape.split(',')])

    pad_output = target_shape[0] == 4
    gpus = list(map(int, filter(None, args.gpus.split(',')))) # filter to not encount eventually empty strings
    batch_size = args.batch_size//len(gpus)

    mx_resnet_print_event(
            key=constants.MODEL_BN_SPAN,
            val=batch_size*args.bn_group)

    num_threads = args.dali_threads

    # the input_layout w.r.t. the model is the output_layout of the image pipeline
    output_layout = types.NHWC if args.input_layout == 'NHWC' else types.NCHW

    (rank, num_workers) = _get_rank_and_worker_count(args, kv)

    data_paths = {}
    if args.dali_cache_size > 0 and args.lazy_init_sanity:
        data_paths["train_data_tmp"] = get_tmp_file()
        data_paths["train_idx_tmp"] = get_tmp_file()
        data_paths["val_data_tmp"] = get_tmp_file()
        data_paths["val_idx_tmp"] = get_tmp_file()
    else:
        data_paths["train_data_tmp"] = args.data_train
        data_paths["train_idx_tmp"] = args.data_train_idx
        data_paths["val_data_tmp"] = args.data_val
        data_paths["val_idx_tmp"] = args.data_val_idx

    trainpipes = [HybridTrainPipe(batch_size      = batch_size * args.input_batch_multiplier,
                                  num_threads     = num_threads,
                                  device_id       = gpu_id,
                                  rec_path        = data_paths["train_data_tmp"],
                                  idx_path        = data_paths["train_idx_tmp"],
                                  shard_id        = gpus.index(gpu_id) + len(gpus)*rank,
                                  num_shards      = len(gpus)*num_workers,
                                  crop_shape      = target_shape[1:],
                                  min_random_area = args.min_random_area,
                                  max_random_area = args.max_random_area,
                                  min_random_aspect_ratio = args.min_random_aspect_ratio,
                                  max_random_aspect_ratio = args.max_random_aspect_ratio,
                                  nvjpeg_padding  = args.dali_nvjpeg_memory_padding * 1024 * 1024,
                                  prefetch_queue  = args.dali_prefetch_queue,
                                  hw_decoder_load = args.dali_hw_decoder_load,
                                  dont_use_mmap   = True if args.dali_dont_use_mmap>=1 else False,
                                  seed            = args.seed,
                                  output_layout   = output_layout,
                                  pad_output      = pad_output,
                                  dtype           = args.dtype,
                                  mlperf_print    = gpu_id == gpus[0],
                                  use_roi_decode  = args.dali_roi_decode,
                                  preallocate_width_hint  = args.dali_preallocate_width,
                                  preallocate_height_hint = args.dali_preallocate_height,
                                  tmp_buffer_hint         = args.dali_tmp_buffer_hint,
                                  decoder_buffer_hint     = args.dali_decoder_buffer_hint,
                                  crop_buffer_hint        = args.dali_crop_buffer_hint,
                                  normalize_buffer_hint   = args.dali_normalize_buffer_hint,
                                  cache_size      = args.dali_cache_size) for gpu_id in gpus]

    valpipes = [HybridValPipe(batch_size     = batch_size,
                              num_threads    = num_threads,
                              device_id      = gpu_id,
                              rec_path       = data_paths["val_data_tmp"],
                              idx_path       = data_paths["val_idx_tmp"],
                              shard_id       = 0 if args.separ_val
                                                 else gpus.index(gpu_id) + len(gpus)*rank,
                              num_shards     = 1 if args.separ_val else len(gpus)*num_workers,
                              crop_shape     = target_shape[1:],
                              nvjpeg_padding = args.dali_nvjpeg_memory_padding * 1024 * 1024,
                              prefetch_queue = args.dali_prefetch_queue,
                              seed           = args.seed,
                              dont_use_mmap  = True if args.dali_dont_use_mmap >=1
                                                    else False,
                              hw_decoder_load = args.dali_hw_decoder_load,
                              resize_shp     = resize,
                              output_layout  = output_layout,
                              pad_output     = pad_output,
                              dtype          = args.dtype,
                              mlperf_print   = gpu_id == gpus[0],
                              preallocate_width_hint = args.dali_preallocate_width,
                              preallocate_height_hint = args.dali_preallocate_height,
                              tmp_buffer_hint        = args.dali_tmp_buffer_hint,
                              decoder_buffer_hint    = args.dali_decoder_buffer_hint,
                              crop_buffer_hint       = args.dali_crop_buffer_hint,
                              normalize_buffer_hint  = args.dali_normalize_buffer_hint,
                              cache_size     = args.dali_cache_size) for gpu_id in gpus] if args.data_val else None


    [trainpipe.build() for trainpipe in trainpipes]

    if args.data_val:
        [valpipe.build() for valpipe in valpipes]

    return lambda args, kv: get_rec_iter(args, trainpipes, valpipes, data_paths, kv)


class DB:
    def __init__(self):
        self.data = None
        self.label = None

class RateMatchInputIterator:
    def __init__(self, iterator, input_batch_size, output_batch_size, total_samples, num_workers):
        self.iterator = iterator
        self.input_batch_size = input_batch_size
        self.output_batch_size = output_batch_size
        self._input_buffer = None
        self._output_buffer = DB()
        self._offset = 0
        self._total_samples_processed = 0
        self.num_workers = num_workers
        self.total_samples = total_samples

    def __iter__(self):
        return self

    def __next__(self):
        if (self._input_buffer is None) or (self._offset >= self.input_batch_size) or (self._total_samples_processed >= self.total_samples):
            if self._total_samples_processed >= self.total_samples:
                self._total_samples_processed -= self.total_samples
                raise StopIteration
            if self._offset >= self.input_batch_size:
                self._offset = 0
            try:
                self._input_buffer = self.iterator.__next__()
            except StopIteration:
                self.iterator.reset()
                self._input_buffer = self.iterator.__next__()
                pass

            # Unlike NDArrayIter, DALIGenericIterator outputs a list of ndarrays
            if isinstance(self._input_buffer, list):
                self._input_buffer = self._input_buffer[0]

            # create output buffers (first iteration only):
            if self._output_buffer.data is None:
                self._output_buffer.data = [i.slice_axis(axis=0, begin=0, end=self.output_batch_size)
                                            for i in self._input_buffer.data]
            if self._output_buffer.label is None:
                self._output_buffer.label = [i.slice_axis(axis=0, begin=0, end=self.output_batch_size)
                                             for i in self._input_buffer.label]

        for o, i in zip(self._output_buffer.data, self._input_buffer.data):
            i.slice_axis(out=o, axis=0,
                         begin=self._offset, end=(self._offset+self.output_batch_size) )

        for o, i in zip(self._output_buffer.label, self._input_buffer.label):
            i.slice_axis(out=o, axis=0,
                         begin=self._offset, end=self._offset+self.output_batch_size)

        self._offset += self.output_batch_size
        self._total_samples_processed += (self.num_workers * self.output_batch_size)
        return [self._output_buffer]

    def reset(self):
        self.iterator.reset()

    @property
    def batch_size(self):
        return self.iterator.batch_size

    @property
    def size(self):
        return self.iterator.size

    def epoch_size(self, pipeline_id=0):
        return self.iterator._pipes[pipeline_id].epoch_size

    def shard_size(self, pipeline_id=0):
        return self.iterator._pipes[pipeline_id].shard_size


def get_rec_iter(args, trainpipes, valpipes, data_paths, kv=None):
    (rank, num_workers) = _get_rank_and_worker_count(args, kv)

    # now data is available in the provided paths to DALI, it ensures that the data has not been touched
    # user need to clean up the /tmp from the created symlinks
    # DALIClassificationIterator() does the init so we need to provide the real data here
    if args.dali_cache_size > 0 and args.lazy_init_sanity:
        link_to_tmp_file(args.data_train, data_paths["train_data_tmp"])
        link_to_tmp_file(args.data_train_idx, data_paths["train_idx_tmp"])
        link_to_tmp_file(args.data_val, data_paths["val_data_tmp"])
        link_to_tmp_file(args.data_val_idx, data_paths["val_idx_tmp"])

    mx_resnet_print_event(
            key=constants.TRAIN_SAMPLES,
            val=args.num_examples)

    dali_train_iter = get_dali_classification_iterator(pipes=trainpipes,
                                                       last_batch_policy=LastBatchPolicy.FILL,
                                                       last_batch_padded=False,
                                                       size=args.num_examples // num_workers,
                                                       reader_name="Reader",
                                                       num_workers=num_workers)

    if args.input_batch_multiplier>1:
        dali_train_iter = RateMatchInputIterator(iterator=dali_train_iter,
                                                 input_batch_size=args.batch_size*args.input_batch_multiplier,
                                                 output_batch_size=args.batch_size,
                                                 total_samples = args.num_examples, num_workers = num_workers)

    if args.num_examples < trainpipes[0].epoch_size("Reader"):
        warnings.warn("{} training examples will be used, although full training set contains {} examples".format(args.num_examples, trainpipes[0].epoch_size("Reader")))

    worker_val_examples = valpipes[0].epoch_size("Reader")
    if (args.num_val_examples>0):
        worker_val_examples = args.num_val_examples

    mx_resnet_print_event(key=constants.EVAL_SAMPLES,
                          val=worker_val_examples)
    if not args.separ_val:
        worker_val_examples = worker_val_examples // num_workers
        if rank < valpipes[0].epoch_size("Reader") % num_workers:
            worker_val_examples += 1

    dali_val_iter = get_dali_classification_iterator(pipes=valpipes,
                                                     last_batch_policy=LastBatchPolicy.PARTIAL,
                                                     last_batch_padded=True,
                                                     size=worker_val_examples,
                                                     reader_name="Reader",
                                                     num_workers=num_workers) if args.data_val else None

    return dali_train_iter, dali_val_iter
