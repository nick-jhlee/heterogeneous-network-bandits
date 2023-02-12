from utils import *
from collections import deque
from scipy.stats import cauchy, levy
from math import inf

from tqdm import tqdm
from copy import deepcopy


class Problem:
    def __init__(self, Network, Agents, T, N, K, param):
        # param: (discard, n_gossip, mp, gamma, p, n_repeat)
        discard, n_gossip, mp, gamma, p, n_repeat = param

        self.Agents = Agents
        self.Network = Network

        self.T, self.N, self.K = T, N, K

        self.discard = discard
        self.n_gossip = n_gossip
        self.mp = mp
        self.gamma = gamma
        self.p = p  # communication probability (1 - link failure)
        self.n_repeat = n_repeat

    def __str__(self):
        return f"discard={self.discard}, n_gossip={self.n_gossip}, {self.mp}"


class Message:
    def __init__(self, arm, reward, hash_value, gamma):
        self.arm = arm
        self.reward = reward
        self.hash_value = hash_value
        self.gamma = gamma

    def decay(self):
        self.gamma -= 1

    def corrupt(self, mp):
        if np.random.binomial(1, 0.5) == 1:
            if "gaussian" in mp:
                self.reward += np.random.normal(0.1, 0.01)
            elif "zero" in mp:
                self.reward = 0
            elif "heavy" in mp:
                self.reward += levy.rvs()


# class Arm:
#     def __init__(self, arm_idx, reward):
#         self.arm_idx = arm_idx
#         self.reward = reward
#
#     def rot(self, rho=1.0):
#         self.reward *= rho


class Agent:
    """
    reward_avgs: dict of reward to its mean
    """

    def __init__(self, idx, arm_set, reward_avgs, Network, gamma, mp, K):
        self.Network = Network
        self.idx = idx
        self.arm_set = arm_set
        self.optimal = max([reward_avgs[arm] for arm in arm_set])  # local optimal reward
        self.messages = deque()
        self.gamma = gamma
        self.mp = mp

        # not available to the agents; this is for implementeing the arm corruption by the environment
        self.total_arm_set = deque(range(K))

        self.reward_avgs = reward_avgs

        # iteration
        self.t = 0

        # his own regret
        self.regret = 0

        # his own communication complexity (# of messages he passed along)
        self.communication = 0

        # including neighbors
        self.total_rewards = dict.fromkeys(arm_set, 0)
        self.total_visitations = dict.fromkeys(arm_set, 0)

        # hash values of messages that he has seen
        self.history = set()

    def UCB_network(self):
        # warm-up
        if self.t < len(self.arm_set):
            final_arm = self.arm_set[self.t]
        else:
            final_arm, tmp = None, -inf
            for arm in self.arm_set:
                m = self.total_visitations[arm]
                reward_estimate = self.total_rewards[arm] / m
                bonus = np.sqrt(3 * np.log(self.t) / m)
                ucb = reward_estimate + bonus
                if tmp < ucb:
                    final_arm = arm
                    tmp = ucb
        # finalize the set of messages to be passed along
        cur_message = self.pull(final_arm)
        self.history.add(cur_message.hash_value)
        self.messages.append(cur_message)

        # empty his current set of messages
        final_messages = self.messages
        if "interfere" in self.mp:
            final_messages = interfere(final_messages)
        self.messages = deque()
        return final_messages

    def pull(self, arm):
        if arm not in self.arm_set:
            raise ValueError(f"{arm} not in the arm set {self.arm_set} attained by agent {self.idx}")

        # update number of visitations
        self.total_visitations[arm] += 1

        # receive/update reward
        # reward = np.random.binomial(1, self.reward_avgs[arm]) # Berounlli reward
        reward = np.random.normal(self.reward_avgs[arm], 1)
        self.total_rewards[arm] += reward

        # update regret
        self.regret += self.optimal - self.reward_avgs[arm]

        # next time step
        self.t += 1

        # message to be sent to his neighbors
        return Message(arm, reward, hash((arm, reward)), self.gamma)

    def store_message(self, message):
        message.decay()
        if "corrupt" in self.mp:
            message.corrupt(self.mp)
        if message.gamma < 0:  # if the message is not yet expired, for MP
            del message
        else:
            self.messages.append(message)

    def receive_message(self, message):
        # delete message if it has already been observed
        if message.hash_value in self.history:
            del message
        else:
            arm, reward = message.arm, message.reward
            self.history.add(message.hash_value)

            self.total_visitations[arm] += 1
            self.total_rewards[arm] += reward

    def receive(self, messages, p_v=1):
        while messages:  # if d is True if d is not empty (canonical way for all collections)
            message = messages.pop()
            if message is not None:
                contain_message = message.arm in self.arm_set
                if np.random.binomial(1, p_v) == 1:  # if discarding does not take place
                    # receive, depending on the communication protocol!
                    if "MP" in self.mp:
                        if "Hitting" in self.mp:
                            if contain_message:
                                self.receive_message(message)
                                del message
                            else:
                                self.store_message(message)
                        else:
                            if contain_message:
                                self.receive_message(message)
                            self.store_message(message)
                    else:
                        del message
                else:
                    del message


def run_ucb(problem, p):
    # reseeding
    np.random.seed()

    # initialize parameters
    T, N = problem.T, problem.N
    Agents, Network = problem.Agents, problem.Network
    discard, n_gossip, mp = problem.discard, problem.n_gossip, problem.mp
    original_edges = list(Network.edges())

    # for logging
    Regrets = [[0 for _ in range(T)] for _ in range(N)]
    Communications = [[0 for _ in range(T)] for _ in range(N)]
    Edge_Messages = [[0 for _ in range(T)] for _ in range(len(original_edges))]

    min_deg = min((d for _, d in Network.degree()))

    # run UCB
    # for t in tqdm(range(T)):
    for t in range(T):
        # # fail edges randomly w.p. 1-p, i.i.d. -> for temporally changing graphs
        # failed_edges = [edge for edge in original_edges if np.random.binomial(1, p) == 0]
        # Network_modified = deepcopy(Network)
        # Network_modified.remove_edges_from(failed_edges)

        total_messages = [[None] for _ in range(N)]
        # single iteration of UCB, for each agent!
        for v in range(N):
            total_messages[v] = Agents[v].UCB_network()
            Regrets[v][t] = Agents[v].regret

        # information sharing(dissemination)
        for v in range(N):
            messages = total_messages[v]

            if mp == "baseline" or ("bandwidth" in mp and len(messages) >= 150):
                del messages
            else:
                neighbors = Network.adj[v]
                # message broadcasting
                while messages:
                    message = messages.pop()
                    # construct neighbors to which the message will be gossiped to (push protocol!)
                    if n_gossip is None or n_gossip >= len(neighbors):
                        gossip_neighbors = neighbors
                    else:
                        gossip_neighbors = np.random.choice(neighbors, size=n_gossip, replace=False)
                    # pass messages
                    for neighbor in gossip_neighbors:
                        message_copy = deepcopy(message)
                        # update communication complexity
                        Agents[v].communication += 1
                        # update number of messages per edge
                        if v < neighbor:
                            Edge_Messages[original_edges.index((v, neighbor))][t] += 1
                        else:
                            Edge_Messages[original_edges.index((neighbor, v))][t] += 1
                        # send messages
                        if np.random.binomial(1, p) == 0:  # message failure
                            Agents[neighbor].receive(deque([None]), Agents[v])
                        else:
                            if discard:
                                Agents[neighbor].receive(deque([message_copy]), min_deg / Network.degree[neighbor])
                            else:
                                Agents[neighbor].receive(deque([message_copy]))
                    # delete the message sent by the originating agent, after communication is complete
                    del message

            Communications[v][t] = Agents[v].communication

    # group regrets and communications
    # Group_Regrets = np.max(np.array(Regrets), axis=0)
    Group_Regrets = np.sum(np.array(Regrets), axis=0)
    Group_Communications = np.sum(np.array(Communications), axis=0)

    # # plotting quantities vs. iteration
    # path = f"heterogeneous/{mp}_n_gossip={n_gossip}_discard={discard}"
    # if not os.path.exists(path):
    #     os.makedirs(path)
    #
    # # plot regrets
    # titles = [f"{mp}: {wise} Regrets ({Network.name}, p={p}, gamma={Agents[0].gamma}, n_gossip={n_gossip}, " \
    #           f"Discard={discard})" for wise in ["Agent-specific", "Group"]]
    # fnames = [f"{path}/Regret_{wise}_{mp}_p={p}_gamma={Agents[0].gamma}_{Network.name}.pdf" for wise in
    #           ["agent", "group"]]
    # plot(Regrets, Group_Regrets, Network, titles, fnames)
    #
    # # plot communications
    # titles = [f"{mp}: {wise} Communications ({Network.name}, p={p}, gamma={Agents[0].gamma}, n_gossip={n_gossip}, " \
    #           f"Discard={discard})" for wise in ["Agent-specific", "Group"]]
    # fnames = [f"{path}/Communication_{wise}_{mp}_p={p}_gamma={Agents[0].gamma}_{Network.name}.pdf" for wise in
    #           ["agent", "group"]]
    # plot(Communications, Group_Communications, Network, titles, fnames)

    # # plot number of messages passed around, for sparse edges only
    # fig, ax = plt.subplots()
    # clrs = sns.color_palette("husl", len(original_edges))
    # xs = range(T)
    #
    # with sns.axes_style("darkgrid"):
    #     for i, color in enumerate(clrs):
    #         ax.plot(xs, Edge_Messages[i], label=f"{original_edges[i]}", c=color)
    #         # ax.fill_between(xs, final_means[i] - final_stds[i], final_means[i] + final_stds[i],
    #         #                 alpha=0.3, facecolor=color)
    #
    #     ax.set_title(f"[{problem.mp}] Number of messages per edge (gamma={problem.gamma}, p={problem.p}, discard={problem.discard}, n_gossip={problem.n_gossip})")
    #     ax.set_xlabel("t")
    #     ax.legend(loc='center left', bbox_to_anchor=(1, 0.5))
    #     plt.savefig(f"heterogeneous_K={problem.K}/Messages_{problem.mp}.pdf", dpi=1200, bbox_inches='tight')
    #     # plt.show()

    return Group_Regrets, Group_Communications


def interfere(messages):
    # if len(tmp) > 5:
    #     self.messages = deque(np.random.choice(self.messages, 5))
    # return self.messages
    messages_arms = deque(message.arm for message in messages)
    for message in messages:
        if messages_arms.count(message.arm) > 1:
            # messages.remove(message)
            return deque([None])
    return messages
