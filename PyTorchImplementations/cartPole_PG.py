import gym
import ptan
import numpy as np
from tensorboardX import SummaryWriter

import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F

GAMMA = 0.99
LEARNING_RATE = 0.0001
ENTROPY_BETA = 0.01
BATCH_SIZE = 8
REWARD_STEPS = 10

class PolicyGradient(nn.Module):
    def __init__(self, input_size, n_actions):
        super(PolicyGradient, self).__init__()

        self.net = nn.Sequential(
            nn.Linear(input_size, 128),
            nn.ReLU(),
            nn.Linear(128, n_actions)
        )
    
    def forward(self, x):
        return self.net(x)


def calc_qvals(rewards):
    res = []
    sum_r = 0.0
    for r in reversed(rewards):
        sum_r *= GAMMA
        sum_r += r
        res.append(sum_r)
    return list(reversed(res))

if __name__=="__main__":
    env = gym.make('CartPole-v0')
    writer = SummaryWriter(logdir="logs/PG", comment="-cartpole-PG")

    net = PolicyGradient(env.observation_space.shape[0], env.action_space.n)

    agent = ptan.agent.PolicyAgent(net, preprocessor=ptan.agent.float32_preprocessor, apply_softmax=True)

    exp_source = ptan.experience.ExperienceSourceFirstLast(env, agent, gamma=GAMMA, steps_count=REWARD_STEPS)

    optimizer = optim.Adam(net.parameters(), lr=LEARNING_RATE)

    total_rewards = []
    done_episodes = 0
    step_idx = 0
    step_rewards = []
    reward_sum = 0.0
    
    batch_states, batch_actions, batch_scales = [], [], []

    for step_idx, exp in enumerate(exp_source):
        reward_sum += exp.reward
        baseline = reward_sum/ (step_idx + 1)
        writer.add_scalar("baseline", baseline, step_idx)
        batch_states.append(exp.state)
        batch_actions.append(exp.action)
        batch_scales.append(exp.reward - baseline)

        new_rewards = exp_source.pop_total_rewards()
        if new_rewards:
            done_episodes += 1
            reward = new_rewards[0]
            total_rewards.append(reward)
            mean_rewards = float(np.mean(total_rewards[-100:]))

            print("{} steps, {:.2f}: reward, {:.2f}: reward_100, {} episodes".format(step_idx, reward, mean_rewards, done_episodes))
            writer.add_scalar("reward", reward, step_idx)
            writer.add_scalar("reward_100", mean_rewards, step_idx)
            writer.add_scalar("episodes", done_episodes, step_idx)

            if mean_rewards > 195:
                print("Completed in {} steps and {} episodes".format(step_idx, done_episodes))
                break
        
        if len(batch_states) < BATCH_SIZE:
            continue
        
        states_v = torch.FloatTensor(batch_states)
        batch_actions_t = torch.LongTensor(batch_actions)
        batch_scales_v = torch.FloatTensor(batch_scales)

        optimizer.zero_grad()
        logits_v = net(states_v)
        log_prob_v = F.log_softmax(logits_v, dim=1)
        log_prob_actions_v = batch_scales_v * log_prob_v[range(BATCH_SIZE),batch_actions_t]
        loss_policy_v = -log_prob_actions_v.mean()

        prob_v = F.softmax(logits_v, dim=1)
        entropy_v = -(prob_v * log_prob_v).sum(dim=1).mean()
        entropy_loss_v = -ENTROPY_BETA * entropy_v
        loss_v = loss_policy_v + entropy_loss_v

        loss_v.backward()
        optimizer.step()

        new_logits_v = net(states_v)
        new_prob_v = F.softmax(new_logits_v, dim=1)
        kl_div_v = -((new_prob_v/prob_v).log() * prob_v).sum(dim=1).mean()
        writer.add_scalar("kl", kl_div_v.item(), step_idx)

        grad_max = 0.0
        grad_means = 0.0
        grad_count = 0.0

        for p in net.parameters():
            grad_max = max(grad_max, p.grad.abs().max().item())
            grad_means += (p.grad ** 2).mean().sqrt().item()
            grad_count += 1

        writer.add_scalar("baseline", baseline, step_idx)
        writer.add_scalar("entropy", entropy_v.item(), step_idx)
        writer.add_scalar("batch_scales", np.mean(batch_scales), step_idx)
        writer.add_scalar("loss_entropy", entropy_loss_v.item(), step_idx)
        writer.add_scalar("loss_policy", loss_policy_v.item(), step_idx)
        writer.add_scalar("total loss", loss_v.item(), step_idx)
        writer.add_scalar("grad_l2", grad_means/grad_count, step_idx)
        writer.add_scalar("grad_max", grad_max, step_idx)

        batch_states.clear()
        batch_actions.clear()
        batch_scales.clear()
    writer.close()