import argparse
from functools import partial
import torch
from torch.distributions import constraints
import numpy as np

import pyro
from pyro import optim
from pyro.infer import TraceEnum_ELBO
from pyro.contrib.oed.eig import barber_agakov_ape
import pyro.contrib.gp as gp

from gp_bayes_opt import GPBayesOptimizer
from models.bayes_linear import (
    zero_mean_unit_obs_sd_lm, group_assignment_matrix, analytic_posterior_cov
)
from ba.guide import Ba_lm_guide

"""
Example builds on the Bayesian regression tutorial [1]. It demonstrates how
to estimate the average posterior entropy (APE) under a model and use it to
make an optimal decision about experiment design.

The context is a Gaussian linear model in which the design matrix `X` is a
one-hot-encoded matrix with 2 columns. This corresponds to the simplest form
of an A/B test. Assume no data has yet be collected. The aim is to find the optimal
allocation of participants to the two groups to maximise the expected gain in
information from actually performing the experiment.

For details of the implementation of average posterior entropy estimation, see
the docs for :func:`pyro.contrib.oed.eig.vi_ape`.

We recommend the technical report from Long Ouyang et al [2] as an introduction
to optimal experiment design within probabilistic programs.

To optimize the APE (which is required to be minimized) we used Gaussian Process
based Bayesian Optimization. See the BO tutorial [3] for details of optimizing noisy
and expensive-to-compute functions in pyro.

[1] ["Bayesian Regression"](http://pyro.ai/examples/bayesian_regression.html)
[2] Long Ouyang, Michael Henry Tessler, Daniel Ly, Noah Goodman (2016),
    "Practical optimal experiment design with probabilistic programs",
    (https://arxiv.org/abs/1608.05046)
[3] ["Bayesian Optimization"](http://pyro.ai/examples/bo.html)
"""

# Set up regression model dimensions
N = 100  # number of participants
p = 2    # number of features
prior_sds = torch.tensor([10., 0.15])

# Model and guide using known obs_sd
model, svi_guide = zero_mean_unit_obs_sd_lm(prior_sds)
guides = {10: Ba_lm_guide((2,), (10, 3), {"w": 2}).guide,
          2: Ba_lm_guide((2,), (2, 3), {"w": 2}).guide}


def estimated_ape(ns):
    """Estimated APE by BA"""
    d = len(ns)
    designs = [group_assignment_matrix(torch.tensor([n1, N-n1])) for n1 in ns]
    X = torch.stack(designs)
    guide = guides[d]
    est_ape = barber_agakov_ape(
        model, X, "y", "w", 10, 800, guide, 
        optim.Adam({"lr": 0.05}), final_num_samples=1000)
    return est_ape


def true_ape(ns):
    """Analytic APE"""
    true_ape = []
    prior_cov = torch.diag(prior_sds**2)
    designs = [group_assignment_matrix(torch.tensor([n1, N-n1])) for n1 in ns]
    for i in range(len(ns)):
        x = designs[i]
        posterior_cov = analytic_posterior_cov(prior_cov, x, torch.tensor(1.))
        true_ape.append(0.5*torch.logdet(2*np.pi*np.e*posterior_cov))
    return torch.tensor(true_ape)


def learn_posterior(y, model):
    vi_parameters = {
        "guide": svi_guide, 
        "optim": optim.Adam({"lr": 0.05}),
        "loss": TraceEnum_ELBO(strict_enumeration_warning=False).differentiable_loss,
        "num_steps": 1000}
    conditioned_model = pyro.condition(model, data={"y": y})
    SVI(conditioned_model, **vi_parameters).run(design)

    def new_model(design):
        trace = poutine.trace(svi_guide).get_trace(design)
        data = {"w": trace.nodes["w"]["value"]}
        ...



def main(num_vi_steps, num_bo_steps):

    pyro.set_rng_seed(42)

    estimators = [true_ape, estimated_ape]
    noises = [0.0001, 0.25]
    num_acqs = [2, 10]

    for f, noise, num_acquisitions in zip(estimators, noises, num_acqs):
        for experiment in range(3):

            # Reset all parameters
            pyro.clear_param_store()

            X = torch.tensor([25., 75.])
            y = f(X)
            gpmodel = gp.models.GPRegression(
                X, y, gp.kernels.Matern52(input_dim=1, lengthscale=torch.tensor(10.)),
                noise=torch.tensor(noise), jitter=1e-6)
            gpmodel.optimize(loss=TraceEnum_ELBO(strict_enumeration_warning=False).differentiable_loss)
            gpbo = GPBayesOptimizer(constraints.interval(0, 100), gpmodel,
                                    num_acquisitions=num_acquisitions)
            # Due to tensor sizes changing we have to clear param store
            pyro.clear_param_store()
            for i in range(num_bo_steps):
                result = gpbo.get_step(f, None, verbose=True)

            print(f.__doc__)
            print(result)

            # Run the experiment
            resultant_design = group_assignment_matrix(torch.tensor(results, N-results))
            y = true_model(resultant_design)
            model = learn_posterior(y, model)

        print("Final posterior entropy", final_posterior_entropy)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A/B test experiment design using VI")
    parser.add_argument("-n", "--num-vi-steps", nargs="?", default=5000, type=int)
    parser.add_argument('--num-bo-steps', nargs="?", default=5, type=int)
    args = parser.parse_args()
    main(args.num_vi_steps, args.num_bo_steps)