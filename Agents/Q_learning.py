import os
import time
from datetime import datetime
from queue import LifoQueue
from threading import Thread

import numpy as np
import tensorflow as tf

from .BaseQ import (DDQN, ReplayBuffer)
from .visual_utils import ohlcPlot, rewardPlot

UPDATE_FREQUENCY = 500
#Epsilon-Greedy Algorithm
FINAL_P = 0.02
INITIAL_P = 1.0
class BestModels():
    def __init__(self):
        self.records = []
    def check(self,episode, score):
        #Is this score greater than any current top 10s?
        #Condition: Only start recording this score if agent is no longer exploring
        if len(self.records) < 10:
            # Just append if there are not enough on the list
            self.records.append((episode, score))

            #Sort the list
            self.records = sorted(self.records, key=lambda x: x[1], reverse=True)
            return True

        insert = None
        for i, _tuple in enumerate(self.records):
            _, _score = _tuple[0], _tuple[1]
            if score <= _score:
                continue
            insert = i
            break

        #Remove from the last index, insert this
        if insert != None:
            self.records.pop()
            self.records.insert(insert, (episode, score))
            return True
        return False
class DQNAgent():

    def __init__(self, env, directory):
        self.env = env
        self.directory = directory
        self.model_directory = os.path.join(self.directory,'episode%s.ckpt')
        self.ddqn = DDQN(env.states.shape[1])
        self.best_model = BestModels()
    def _episodeLoop(self,
            session,
            random_choice,
            episode,
            batch_size,
            observation,
            learning_step,
            replaybuffer):
        if random_choice:
            action = np.random.choice(self.ddqn.actions)
        else :
            #Pick an action using online network
            action = self.ddqn.choose_action(
                observation=observation,
                session=session)

        #Advance one step with the action in our environment
        new_observation, _action, reward, done = self.env.step(action)

        #Add the Experience to the memory
        replaybuffer.add(observation, _action, reward, new_observation, float(done))

        observation = new_observation
        learning_step += 1

        if learning_step > UPDATE_FREQUENCY:
            #Optimize online network with SGD
            self.ddqn.mini_batch_training(session, replaybuffer, batch_size)

        if learning_step % UPDATE_FREQUENCY == 0:
            #Periodically copy online net to target net
            self.ddqn.update(session)
        return done, learning_step ,observation
    def _useRondomChoice(self,learning_step,total_steps):
        #Pick the decayed epsilon value
        # linear decay
        '''Linearly decay epsilon'''
        if learning_step >= total_steps:
            epsilon =  FINAL_P
        else :
            if total_steps > 0:
                difference = (FINAL_P - INITIAL_P) / total_steps
            epsilon =  INITIAL_P + difference * learning_step

        # linear decay end
        return np.random.random_sample() < epsilon, epsilon
    def _afterDone(self,session,record_episode_after,episode,exploration):
        #Close the Last Trade in portfolio if any
        if self.env.portfolio.trade is not None:
            lastTime = self.env.data.index[self.env.curr_idx].to_pydatetime()
            lastOpen = self.env.data['Open'].iloc[self.env.curr_idx]
            self.env.portfolio.closeTrade(time=lastTime, open=lastOpen)

        #Update Bookkeeping Tools
        average_pips_per_trade = self.env.portfolio.total_reward / self.env.portfolio.total_trades
        self.trades.append(self.env.portfolio.trades)
        self.avg_rewards.append(average_pips_per_trade)
        self.total_rewards.append(self.env.portfolio.total_reward)
        self.equity_curves.append(self.env.portfolio.equity_curve)


        #Print statements at the end of every statements
        print("End of Episode %s, Total Reward is %s, Average Reward is %.3f"%(
            episode,
            self.env.portfolio.total_reward,
            average_pips_per_trade
            ))
        print("Percentage of time spent on exploring (Random Action): %s %%"%(
            int(100 * exploration)))

        #Is this score greater than any current top 10s?
        #Condition: Only start recording this score if agent is no longer exploring
        if episode > record_episode_after and \
            self.best_model.check(episode, average_pips_per_trade):
            saver = tf.train.Saver(max_to_keep=None)
            saver.save(session, self.model_directory % episode)
    def train(
        self,
        batch_size = 32,
        convergence_reward = 2000,
        record_episode_after = 30,
        train_episodes = 200
        ):

        #Clear all previous tensor models
        for file in os.listdir(self.directory):
            os.remove(os.path.join(self.directory, file))

        step_per_episode = self.env.train_end_idx - 2
        total_steps = record_episode_after * step_per_episode
        #Create a Transition memory storage
        replaybuffer = ReplayBuffer(step_per_episode * train_episodes * 1.2)

        #Use of parallelism
        config_proto=tf.ConfigProto(
            inter_op_parallelism_threads=8,
            intra_op_parallelism_threads=8
            )

        session = tf.Session(config=config_proto)

        #Initialize all weights and biases in NNs
        session.run(tf.global_variables_initializer())

        #Update Target Network to Online Network
        self.ddqn.update(session)

        self.trades = []
        self.total_rewards = []
        self.avg_rewards = []
        self.equity_curves = []
        learning_step = 0

        for episode in range(1, train_episodes+1):
            observation = self.env.reset(training=True)
            while True:
                random_choice, exploration = self._useRondomChoice(learning_step,total_steps)
                done, learning_step, observation = self._episodeLoop(
                    session,
                    random_choice,
                    episode,
                    batch_size,
                    observation,
                    learning_step,
                    replaybuffer)
                if done :
                    break
            self._afterDone(session,record_episode_after,episode,exploration)
            if exploration == FINAL_P and \
                len(self.best_model.records) == 10 and \
                np.mean(self.total_rewards[-16:-1]) > convergence_reward: # 最後の15個が予定のrewardを超えていたら
                print ("CONVERGED!")
                break
    def trainSummary(self, TOP_N=3):

        #Plot Total Reward
        rewardPlot(self.total_rewards, self.best_model.records, 'Total', TOP_N)
        for i,m in enumerate(self.best_model.records):
            episode = m[0]
            print ("########   RANK {}   ###########".format(i+1))
            print ("Episode          | {}".format(episode))
            print ("Total Reward     | {0:.2f}".format(self.total_rewards[episode-1]))
            print ("Average Reward   | {0:.2f}".format(self.avg_rewards[episode-1]))

    def episodeReview(self, episode):
        index = episode - 1
        ohlcPlot(self.trades[index], self.env.data, self.equity_curves[index])
    def test(self, episode):
        '''
        episode: int, episode to be selected
        '''
        assert len(os.listdir(self.directory)) > 0, "No saved tensor models are found for this model, please train the network"

        session = tf.Session()
        saver = tf.train.Saver()
        saver.restore(session, self.model_directory % episode)

        observation = self.env.reset(training=False)
        while True:
            #Select Action
            action = self.ddqn.choose_action(
                    observation=observation,
                    session=session,
                    test=True)

            #Transit to next state given action
            observation, _, _, done = self.env.step(action)
            if done:
                break;

        average_pips_per_trade = self.env.portfolio.total_reward / self.env.portfolio.total_trades
        self.trades.append(self.env.portfolio.trades)
        self.avg_rewards.append(average_pips_per_trade)
        self.total_rewards.append(self.env.portfolio.total_reward)
        self.equity_curves.append(self.env.portfolio.equity_curve)
        self.episodeReview(0)
