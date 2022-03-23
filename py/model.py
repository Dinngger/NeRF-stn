#-*-coding:utf-8-*-

"""
    NeRF network details. To be finished ...
"""

import torch
from torch import nn
from time import time
from matplotlib import image
from dataset import CustomDataSet
from nerf_helper import sampling, encoding
from torch.nn import functional as F
from utils import inverseSample, fov2Focal
from torchvision.transforms import transforms

def makeMLP(in_chan, out_chan, act = nn.ReLU(inplace = True), batch_norm = False):
    modules = [nn.Linear(in_chan, out_chan)]
    if batch_norm == True:
        modules.append(nn.BatchNorm1d(out_chan))
    if not act is None:
        modules.append(act)
    return modules

# This module is shared by coarse and fine network, with no need to modify
class NeRF(nn.Module):
    def __init__(self, position_flevel, direction_flevel) -> None:
        super().__init__()
        self.position_flevel = position_flevel
        self.direction_flevel = direction_flevel

        module_list = makeMLP(60, 256)
        for _ in range(4):
            module_list.extend(makeMLP(256, 256))

        self.lin_block1 = nn.Sequential(*module_list)       # MLP before skip connection
        self.lin_block2 = nn.Sequential(
            *makeMLP(316, 256),
            *makeMLP(256, 256), *makeMLP(256, 256),
            *makeMLP(256, 256, None)
        )

        self.opacity_head = nn.Sequential(                  # authors said that ReLU is used here
            *makeMLP(256, 1)
        )
        self.rgb_layer = nn.Sequential(
            *makeMLP(280, 128),
            *makeMLP(128, 3, nn.Sigmoid())
        )

    def loadFromFile(self, load_path:str):
        save = torch.load(load_path)   
        save_model = save['model']                  
        model_dict = self.state_dict()
        state_dict = {k:v for k, v in save_model.items()}
        model_dict.update(state_dict)
        self.load_state_dict(model_dict) 
        print("Swin Transformer Model loaded from '%s'"%(load_path))

    # for coarse network, input is obtained by sampling, sampling result is (ray_num, point_num, 9), (depth) (ray_num, point_num)
    # TODO: fine-network输入的point_num是192，会产生影响吗？
    def forward(self, pts:torch.Tensor) -> torch.Tensor:
        flat_batch = pts.shape[0] * pts.shape[1]
        position_dim, direction_dim = 6 * self.position_flevel, 6 * self.direction_flevel
        encoded_x:torch.Tensor = torch.zeros(flat_batch, position_dim).cuda()
        encoded_r:torch.Tensor = torch.zeros(flat_batch, direction_dim).cuda()
        encoding(pts[:, :, :3].view(-1, 3), encoded_x, self.position_flevel, False)
        encoding(pts[:, :, 3:6].view(-1, 3), encoded_r, self.direction_flevel, False)
        encoded_x = encoded_x.view(pts.shape[0], pts.shape[1], position_dim)
        encoded_r = encoded_r.view(pts.shape[0], pts.shape[1], direction_dim)

        tmp = self.lin_block1(encoded_x)
        encoded_x = torch.cat((tmp, encoded_x), dim = -1)
        encoded_x = self.lin_block2(encoded_x)
        opacity = self.opacity_head(encoded_x)
        rgb = self.rgb_layer(torch.cat((encoded_x, encoded_r), dim = -1))
        return torch.cat((rgb, opacity), dim = -1)

    """
        This function is important for inverse transform sampling, since for every ray
        we will have 64 normalized weights (summing to 1.) for inverse sampling
    """
    @staticmethod
    def getNormedWeight(opacity:torch.Tensor, depth:torch.Tensor) -> torch.Tensor:
        delta:torch.Tensor = torch.hstack((depth[:, 1:] - depth[:, :-1], torch.FloatTensor([1e9]).repeat((depth.shape[0], 1)).cuda()))
        mult:torch.Tensor = -opacity * delta

        ts:torch.Tensor = torch.exp(torch.hstack((torch.zeros(mult.shape[0], 1, dtype = torch.float32).cuda(), torch.cumprod(mult)[:, :-1])))
        alpha:torch.Tensor = 1. - torch.exp(mult)       # shape (ray_num, point_num)
        # fusion requires normalization, rgb output should be passed through sigmoid
        weights:torch.Tensor = ts * alpha               # shape (ray_num, point_num)
        return torch.sum(weights, dim = -1, keepdim = True)

    @staticmethod
    def render(rgbo:torch.Tensor, depth:torch.Tensor) -> torch.Tensor:
        rgb:torch.Tensor = rgbo[..., :3] # shape (ray_num, pnum, 3)
        # RGB passed through sigmoid
        rgb_normed:torch.Tensor = F.sigmoid(rgb)

        opacity:torch.Tensor = rgbo[..., -1]
        weights_normed:torch.Tensor = NeRF.getNormedWeight(opacity, depth)

        weighted_rgb:torch.Tensor = weights_normed[:, :, None] * rgb_normed
        return torch.sum(weighted_rgb, dim = 1)

"""
Latest TODO:
- test模块（全图sample）
- 训练可执行文件
"""

TEST_RAY_NUM = 1024
TEST_PNUM = 64
TEST_NEAR_T = 2.
TEST_FAR_T = 6.

if __name__ == "__main__":
    torch.set_default_tensor_type(torch.FloatTensor)
    nerf_model = NeRF(10, 4).cuda()
    dataset = CustomDataSet("../../dataset/nerf_synthetic/drums/", transforms.ToTensor(), True)
    cam_fov, tfs, images = dataset.get_dataset(to_cuda = True)
    output:torch.Tensor = torch.zeros(TEST_RAY_NUM, TEST_PNUM, 9, dtype = torch.float32).cuda()
    lengths:torch.Tensor = torch.zeros(TEST_RAY_NUM, TEST_RAY_NUM, dtype = torch.float32).cuda()
    focal = fov2Focal(cam_fov, images.shape[2])
    sampling(images, tfs, output, lengths, TEST_RAY_NUM, TEST_PNUM, focal, TEST_NEAR_T, TEST_FAR_T)
    start_time = time()
    rgbo = nerf_model(output)
    end_time = time()
    print("Finished forwarding within %.6lf seconds. Shape:"%(end_time - start_time), rgbo.shape)
    