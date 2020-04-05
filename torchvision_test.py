import numpy as np
import torch
import tvm
from tvm import relay
from torchvision import models
from tvm.relay.frontend.pytorch import from_pytorch


class SegmentationModelWrapper(torch.nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model

    def forward(self, inp):
        out = self.model(inp)
        return out["out"]


class DetectionModelWrapper(torch.nn.Module):
    def __init__(self, model, output_key):
        super().__init__()
        self.model = model
        self.output_key = output_key

    def forward(self, inp):
        out = self.model(inp)
        return out[0][self.output_key]


def run_on_models(models, inputs, target="llvm"):
    for raw_model in models:
        with torch.no_grad():
            pt_result = raw_model(*inputs).numpy()
            script_module = torch.jit.trace(raw_model, *inputs).eval()

        num_inputs = len(inputs)
        input_names = ["input%d" % i for i in range(num_inputs)]
        input_shapes = list(zip(input_names, [inp.shape for inp in inputs]))
        mod, params = from_pytorch(script_module, input_shapes)

        with relay.build_config(opt_level=3):
            json, lib, params = relay.build(mod, target=target, params=params)

        ctx = tvm.context(target, 0)
        runtime = tvm.contrib.graph_runtime.create(json, lib, ctx)
        runtime.set_input(**params)
        for name, inp in zip(input_names, inputs):
            runtime.set_input(name, inp.numpy())
        runtime.run()

        tvm_result = runtime.get_output(0).asnumpy()
        np.allclose(tvm_result, pt_result, rtol=1e-5, atol=1e-5)
        print(np.max(np.abs(tvm_result - pt_result)),
              np.mean(np.abs(tvm_result - pt_result)))

        tvm.testing.assert_allclose(tvm_result, pt_result,
                                    rtol=1e-3, atol=1e-3)


def imagenet_test():
    inp = torch.rand(1, 3, 224, 224, dtype=torch.float)

    test_models = [
        models.resnet.resnet18(pretrained=True).eval(),
        models.mobilenet.mobilenet_v2(pretrained=True).eval(),
        models.squeezenet.squeezenet1_1(pretrained=True).eval(),
        models.densenet.densenet121(pretrained=True).eval(),
        models.inception.inception_v3(pretrained=True).eval(),
        models.mnasnet.mnasnet1_0(pretrained=True).eval(),
        models.alexnet(pretrained=True).eval(),
        models.vgg.vgg11_bn(pretrained=True).eval(),
    ]

    for target in ["llvm"]:
        run_on_models(test_models, [inp], target)


def segmentation_test():
    inp = torch.rand((1, 3, 300, 300), dtype=torch.float)

    fcn = models.segmentation.fcn_resnet101(pretrained=True).eval()
    deeplab = models.segmentation.deeplabv3_resnet101(pretrained=True).eval()

    test_models = [
       SegmentationModelWrapper(fcn),
       SegmentationModelWrapper(deeplab),
    ]

    for target in ["llvm"]:
        run_on_models(test_models, [inp], target)


def detection_test():
    input_name = 'X'
    input_shapes = {input_name: (1, 3, 100, 100)}
    inp = torch.rand(input_shapes[input_name], dtype=torch.float)

    test_models = []
    for model_func, output_key in zip([models.detection.maskrcnn_resnet50_fpn,
                                       models.detection.fasterrcnn_resnet50_fpn],
                                      ["masks", "boxes"]):
        detection_model = model_func(num_classes=50, pretrained_backbone=False)
        test_models.append(DetectionModelWrapper(detection_model.eval(), output_key))

    # for target in ["llvm"]:
    #     run_on_models(test_models, inp, input_shapes, target)


def resnet3d_test():
    input_shape = (1, 3, 4, 112, 112)
    inputs = [torch.rand(input_shape)]
    model = models.video.r3d_18(pretrained=True).eval()
    run_on_models([model], inputs, "llvm")


imagenet_test()
segmentation_test()
detection_test()
resnet3d_test()
