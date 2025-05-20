#! /usr/bin/env python

from utils import create_cluster, delete_nodes

import argparse

parser = argparse.ArgumentParser("Manages Openstack VMs")

parser.add_argument(
    "--clouds",
    type=str,
    default="credentials/clouds.yaml",
    help="Path to the clouds.yaml file",
)

command_parser = parser.add_subparsers(
    dest="command", required=True, help="Command to manage a Cluster"
)

create_parser = command_parser.add_parser("create", help="Create a new VM instance")
delete_parser = command_parser.add_parser("delete", help="Delete a VM instance")


create_parser.add_argument(
    "--nodes", default=1, type=int, help="Number of nodes to create"
)

delete_parser.add_argument(
    "--hosts-file",
    type=str,
    help="Path to the hosts.yaml file",
    default="./config/hosts.yaml",
)


create_parser.set_defaults(func=create_cluster)
delete_parser.set_defaults(func=delete_nodes)
args = parser.parse_args()


if __name__ == "__main__":
    args.func(args)
