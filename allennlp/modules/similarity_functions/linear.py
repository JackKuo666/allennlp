from typing import Callable

from overrides import overrides
import torch
from torch.nn.parameter import Parameter

from allennlp.common import Params
from allennlp.common.checks import ConfigurationError
from allennlp.modules.similarity_function import SimilarityFunction


@SimilarityFunction.register("linear")
class LinearSimilarity(SimilarityFunction):
    """
    This similarity function performs a dot product between a vector of weights and some
    combination of the two input vectors, followed by an (optional) activation function.  The
    combination used is configurable.

    If the two vectors are ``x`` and ``y``, we allow the following kinds of combinations: ``x``,
    ``y``, ``x*y``, ``x+y``, ``x-y``, ``x/y``, where each of those binary operations is performed
    elementwise.  You can list as many combinations as you want, comma separated.  For example, you
    might give ``x,y,x*y`` as the ``combination`` parameter to this class.  The computed similarity
    function would then be ``w^T [x; y; x*y] + b``, where ``w`` is a vector of weights, ``b`` is a
    bias parameter, and ``[;]`` is vector concatenation.

    Note that if you want a bilinear similarity function with a diagonal weight matrix W, where the
    similarity function is computed as `x * w * y + b` (with `w` the diagonal of `W`), you can
    accomplish that with this class by using "x*y" for `combination`.

    Parameters
    ----------
    tensor_1_dim : ``int``
        The dimension of the first tensor, ``x``, described above.  This is ``x.size()[-1]`` - the
        length of the vector that will go into the similarity computation.  We need this so we can
        build weight vectors correctly.
    tensor_2_dim : ``int``
        The dimension of the second tensor, ``y``, described above.  This is ``y.size()[-1]`` - the
        length of the vector that will go into the similarity computation.  We need this so we can
        build weight vectors correctly.
    combination : ``str``, optional (default=``"x,y"``)
        Described above.
    activation : ``Callable[[torch.Tensor], torch.Tensor]``, optional (default=``lambda x: x``)
        An activation function applied after the ``w^T * [x;y] + b`` calculation.  Default is no
        activation.
    """
    def __init__(self,
                 tensor_1_dim: int,
                 tensor_2_dim: int,
                 combination: str = 'x,y',
                 activation: Callable[[torch.Tensor], torch.Tensor] = lambda x: x) -> None:
        super(LinearSimilarity, self).__init__()
        self._combinations = combination.split(',')
        combined_dim = self._get_combined_dim(tensor_1_dim, tensor_2_dim)
        self._weight_vector = Parameter(torch.Tensor(combined_dim))
        self._bias = Parameter(torch.Tensor(1))
        self._activation = activation

    @overrides
    def forward(self, tensor_1: torch.Tensor, tensor_2: torch.Tensor) -> torch.Tensor:
        combined_tensors = self._combine_tensors(tensor_1, tensor_2)

        # The '@' operator here is torch.matmul, but that's only available in pytorch-0.2.
        # TODO(mattg): switch to using torch.matmul when a version of pytorch with it is released.
        # I think it's more clear and less magical, and won't require us to have to special case
        # the higher-order version.
        # Also, broadcasting this simple addition is only available in pytorch-0.2.  When that's
        # ready, change this back to `(dot_product + self._bias)`.
        if combined_tensors.dim() <= 2:
            dot_product = combined_tensors @ self._weight_vector
        else:
            view_args = [-1] + list(combined_tensors.size()[-2:])
            reshaped_tensor = combined_tensors.view(*view_args)
            unsqueezed_weight = self._weight_vector.unsqueeze(1).unsqueeze(0)
            reshaped_weight = unsqueezed_weight.expand(reshaped_tensor.size()[0],
                                                       self._weight_vector.size()[0],
                                                       1)
            reshaped_dot_product = reshaped_tensor.bmm(reshaped_weight)
            view_args = combined_tensors.size()[:-1]
            dot_product = reshaped_dot_product.view(*view_args)
        return self._activation(dot_product + self._bias.expand_as(dot_product)).squeeze(dim=-1)

    def _combine_tensors(self, tensor_1: torch.Tensor, tensor_2: torch.Tensor) -> torch.Tensor:
        combined_tensor = self._get_combination(self._combinations[0], tensor_1, tensor_2)
        for combination in self._combinations[1:]:
            to_concatenate = self._get_combination(combination, tensor_1, tensor_2)
            combined_tensor = torch.cat([combined_tensor, to_concatenate], dim=-1)
        return combined_tensor

    def _get_combination(self, combination: str, tensor_1, tensor_2):
        if combination == 'x':
            return tensor_1
        elif combination == 'y':
            return tensor_2
        else:
            if len(combination) != 3:
                raise ConfigurationError("Invalid combination: " + combination)
            first_tensor = self._get_combination(combination[0], tensor_1, tensor_2)
            second_tensor = self._get_combination(combination[2], tensor_1, tensor_2)
            operation = combination[1]
            if operation == '*':
                return first_tensor * second_tensor
            elif operation == '/':
                return first_tensor / second_tensor
            elif operation == '+':
                return first_tensor + second_tensor
            elif operation == '-':
                return first_tensor - second_tensor
            else:
                raise ConfigurationError("Invalid operation: " + operation)

    def _get_combined_dim(self, tensor_1_dim: int, tensor_2_dim: int) -> int:
        combination_dims = [self._get_combination_dim(combination, tensor_1_dim, tensor_2_dim)
                            for combination in self._combinations]
        return sum(combination_dims)

    def _get_combination_dim(self, combination: str, tensor_1_dim: int, tensor_2_dim: int) -> int:
        if combination == 'x':
            return tensor_1_dim
        elif combination == 'y':
            return tensor_2_dim
        else:
            if len(combination) != 3:
                raise ConfigurationError("Invalid combination: " + combination)
            first_tensor_dim = self._get_combination_dim(combination[0], tensor_1_dim, tensor_2_dim)
            second_tensor_dim = self._get_combination_dim(combination[2], tensor_1_dim, tensor_2_dim)
            operation = combination[1]
            if first_tensor_dim != second_tensor_dim:
                raise ConfigurationError("Tensor dims must match for operation \"{}\"".format(operation))
            return first_tensor_dim

    @classmethod
    def from_params(cls, params: Params) -> 'LinearSimilarity':
        tensor_1_dim = params.pop("tensor_1_dim")
        tensor_2_dim = params.pop("tensor_2_dim")
        combination = params.pop("combination", "x,y")
        # TODO(mattg): figure out activation from_params.
        activation = lambda x: x
        params.assert_empty(cls.__name__)
        return cls(tensor_1_dim=tensor_1_dim,
                   tensor_2_dim=tensor_2_dim,
                   combination=combination,
                   activation=activation)