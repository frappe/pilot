import uvicorn

from pilot.config.common import CommonConfig
from pilot.core.database.agent.app import create_app
from pilot.core.server import Server


def main() -> None:
    """Serve the database agent on the address and port in common_config.toml."""
    config = CommonConfig.read(Server().benches_dir)
    uvicorn.run(create_app(config), host=config.database_agent.address, port=config.database_agent.port)


if __name__ == "__main__":
    main()
