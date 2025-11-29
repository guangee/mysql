build() {
  image=$1
  path=$2
  docker build -t ${docker_hub_username}/${image} ./${path}
  docker push ${docker_hub_username}/${image}
}

build "$1" "$2"
