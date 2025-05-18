#! /usr/bin/env python

from concurrent.futures import ThreadPoolExecutor, as_completed

from copy import copy
from openstack import connection
from openstack.config import loader
import yaml
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


def read_instance_config(filename):
    with open(filename, "r") as file:
        instance_config = yaml.safe_load(file)
    return instance_config


def get_instance_ips(instance):
    ips = {"floating": None, "fixed": None}
    if instance.addresses:
        for network, addresses_info in instance.addresses.items():
            for a in addresses_info:
                if a["OS-EXT-IPS:type"] == "floating":
                    ips["floating"] = a["addr"]
                else:
                    ips["fixed"] = a["addr"]
            return ips
    else:
        return None


def create_instance(conn, instance_dict_or_path, userdata_filename, postfix=""):
    if isinstance(instance_dict_or_path, str):
        instance_config = read_instance_config(instance_dict_or_path)
    else:
        instance_config = copy(instance_dict_or_path)
    instance_config["name"] = f"{instance_config['name']}{postfix}"
    with open(userdata_filename, "r") as file:
        server = conn.create_server(**instance_config, wait=True, userdata=file)
        ips = get_instance_ips(server)
    print(f"Created server: {server.name} with ID: {server.id} and IP: {ips}")
    return server, ips


def delete_instance(conn, instance_id):
    try:
        server = conn.get_server(instance_id)
        if server:
            conn.delete_server(server.id, wait=True)
            print(f"Deleted server: {server.name} with ID: {server.id}")
        else:
            print(f"Server with ID {instance_id} not found.")
    except Exception as e:
        print(f"Error deleting server: {e}")


def create_connection(clouds_filename):
    config = loader.OpenStackConfig(config_files=[clouds_filename]).get_one_cloud(
        "openstack"
    )
    conn = connection.from_config(cloud="openstack", config=config)
    return conn


def create_nodes(conn, instance_config, userdata, n_nodes):
    nodes = [None for _ in range(n_nodes)]
    with ThreadPoolExecutor(max_workers=n_nodes) as executor:
        futures_to_nodeid = {}
        for i in range(n_nodes):
            future = executor.submit(
                create_instance,
                conn,
                instance_config,
                userdata,
                postfix=f"-node-{i}",
            )
            futures_to_nodeid[future] = i
        for future in as_completed(futures_to_nodeid):
            node_idx = futures_to_nodeid[future]
            node_instance, node_ips = future.result()
            node_host = {}
            node_host["addresses"] = node_ips
            node_host["id"] = node_instance.id
            nodes[node_idx] = node_host
    return nodes


def create_hostnames(nodes):
    hostnames = {}
    for i, node in enumerate(nodes):
        hostnames[f"node-{i}"] = node["addresses"]["fixed"]
    hosts_string = "\n      ".join([f"{v} {k}" for k, v in hostnames.items()])
    return hosts_string


def update_userdata(userdata, nodes):
    with open(userdata, "r") as file:
        userdata_content = file.read()
        userdata_content_updated = userdata_content.replace(
            "NODENAMES", create_hostnames(nodes)
        )
    new_userdata = userdata[:-5] + "_updated.yaml"
    with open(new_userdata, "w") as file:
        file.write(userdata_content_updated)
    return new_userdata


def create_driver(conn, instance_config, userdata, nodes):
    updated_userdata = update_userdata(userdata, nodes)
    driver_instance, driver_ips = create_instance(
        conn, instance_config, updated_userdata, postfix="-driver"
    )
    driver_host = {}
    driver_host["addresses"] = driver_ips
    driver_host["id"] = driver_instance.id
    return driver_host


def create_inventories(nodes):
    with open("./ansible/inventory.yaml", "w") as file:
        inventory = {
            "kube_control_plane": {
                "hosts": {"node-0": {"host": nodes[0]["addresses"]["fixed"]}}
            },
            "kube_workers": {
                "hosts": {
                    f"node-{i + 1}": {"host": node["addresses"]["fixed"]}
                    for i, node in enumerate(nodes[1:])
                }
            },
        }
        yaml.dump(inventory, file)


def create_cluster(args):
    hosts = {"driver": {}, "nodes": []}
    conn = create_connection("credentials/clouds.yaml")

    # Create nodes
    node_instance_config = read_instance_config("./config/instance/node.yaml")
    node_userdata = "./config/instance/node_userdata.yaml"
    nodes = create_nodes(conn, node_instance_config, node_userdata, args.nodes)
    hosts["nodes"] = nodes
    with open("./config/hosts.yaml", "w") as file:
        yaml.dump(hosts, file)

    # create ansible inventory
    create_inventories(nodes)

    # Create driver
    driver_userdata = "./config/instance/driver_userdata.yaml"
    driver_instance_config = read_instance_config("./config/instance/driver.yaml")
    driver = create_driver(conn, driver_instance_config, driver_userdata, nodes)

    hosts["driver"] = driver

    with open("./config/hosts.yaml", "w") as file:
        yaml.dump(hosts, file)


def delete_nodes(args):
    conn = create_connection("credentials/clouds.yaml")
    with open(args.hosts_file, "r") as file:
        hosts = yaml.safe_load(file)
    driver_id = hosts["driver"]["id"]
    delete_instance(conn, driver_id)

    for node in hosts["nodes"]:
        node_id = node["id"]
        delete_instance(conn, node_id)

    hosts = {"driver": {}, "nodes": []}
    with open(args.hosts_file, "w") as file:
        yaml.dump(hosts, file)


create_parser.set_defaults(func=create_cluster)
delete_parser.set_defaults(func=delete_nodes)
args = parser.parse_args()


if __name__ == "__main__":
    args.func(args)
